"""
CSFloat Alert Bot para Discord
================================
Monitorea listings en CSFloat y avisa cuando un skin esta
X% mas barato que el precio mediano actual en CSFloat.
"""

import requests
import os
import statistics
import discord
from discord.ext import commands, tasks
from collections import defaultdict
from datetime import datetime

# ============================================================
# ⚙️  CONFIGURACIÓN
# ============================================================

DISCORD_TOKEN    = os.environ.get("DISCORD_TOKEN", "")
CANAL_ALERTAS_ID = int(os.environ.get("CANAL_ALERTAS_ID", "0"))
CSFLOAT_API_KEY  = os.environ.get("CSFLOAT_API_KEY", "")

# Descuento mínimo vs precio mediano de CSFloat para alertar
DESCUENTO_MINIMO = 10      # % vs mediano de CSFloat

PRECIO_MIN_USD   = 1
PRECIO_MAX_USD   = 99999
INTERVALO_MINUTOS = 1

# Mínimo de listings del mismo skin para calcular mediano confiable
MIN_LISTINGS_PARA_MEDIANO = 2

# ============================================================

CSFLOAT_BASE = "https://csfloat.com/api/v1"

alertas_enviadas  = set()
bot_pausado       = False
total_alertas     = 0
inicio_bot        = datetime.now()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# 🔧 FUNCIONES DE CSFLOAT
# ============================================================

def get_listings():
    """Obtiene hasta 500 listings activos de CSFloat usando paginación."""
    url = f"{CSFLOAT_BASE}/listings"
    headers = {"Authorization": CSFLOAT_API_KEY}
    todos = []

    for page in range(10):
        params = {
            "type": "buy_now",
            "min_price": int(PRECIO_MIN_USD * 100),
            "max_price": int(PRECIO_MAX_USD * 100),
            "limit": 50,
            "sort_by": "most_recent",
            "page": page,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"[DEBUG] Pagina {page} — Status: {resp.status_code}")
            if resp.status_code != 200:
                print(f"[DEBUG] Respuesta: {resp.text[:200]}")
                break
            data = resp.json()
            items = data.get("data", data.get("listings", [])) if isinstance(data, dict) else data
            if not items:
                break
            todos.extend(items)
        except Exception as e:
            print(f"[ERROR] Pagina {page}: {e}")
            break

    print(f"[DEBUG] Total listings obtenidos: {len(todos)}")
    return todos


def agrupar_por_skin(listings):
    """Agrupa listings por nombre de skin."""
    grupos = defaultdict(list)
    for item in listings:
        try:
            nombre     = item["item"].get("market_hash_name", "Desconocido")
            precio     = item["price"] / 100
            float_val  = item["item"].get("float_value", 0)
            listing_id = item["id"]
            is_st      = item["item"].get("is_stattrak", False)
            rareza     = item["item"].get("rarity_name", "")
            grupos[nombre].append({
                "precio": precio,
                "id": listing_id,
                "float": float_val,
                "rareza": rareza,
                "is_st": is_st,
            })
        except (KeyError, TypeError):
            continue
    return grupos


def build_embed(nombre, precio, mediano, descuento, float_val, rareza, is_st, url_skin, total_listings):
    st_tag = "StatTrak™ " if is_st else ""
    if descuento >= 30:
        color = discord.Color.red()
    elif descuento >= 20:
        color = discord.Color.orange()
    else:
        color = discord.Color.green()

    embed = discord.Embed(
        title=f"🔥 {st_tag}{nombre}",
        url=url_skin,
        description=f"Skin detectado con **{descuento:.1f}% de descuento** vs mediano CSFloat",
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="💰 Precio", value=f"**${precio:.2f}**", inline=True)
    embed.add_field(name="📊 Mediano CSFloat", value=f"${mediano:.2f}", inline=True)
    embed.add_field(name="📉 Descuento", value=f"**{descuento:.1f}%**", inline=True)
    embed.add_field(name="🔢 Float", value=f"`{float_val:.6f}`", inline=True)
    embed.add_field(name="💎 Rareza", value=rareza or "N/A", inline=True)
    embed.add_field(name="📋 Listings del skin", value=str(total_listings), inline=True)
    embed.add_field(name="🔗 Link", value=f"[Ver en CSFloat]({url_skin})", inline=True)
    embed.set_footer(text="CSFloat Alert Bot")
    return embed


# ============================================================
# ⏱️ TAREA PERIÓDICA
# ============================================================

@tasks.loop(minutes=INTERVALO_MINUTOS)
async def monitorear():
    global total_alertas, bot_pausado

    if bot_pausado:
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Revisando mercado...")
    canal = bot.get_channel(CANAL_ALERTAS_ID)
    if canal is None:
        print("[ERROR] No se encontro el canal.")
        return

    listings = get_listings()
    if not listings:
        print("No se obtuvieron listings.")
        return

    grupos = agrupar_por_skin(listings)
    print(f"[DEBUG] Skins unicos encontrados: {len(grupos)}")

    for nombre, items in grupos.items():
        try:
            # Necesitamos al menos MIN_LISTINGS_PARA_MEDIANO para calcular mediano confiable
            if len(items) < MIN_LISTINGS_PARA_MEDIANO:
                continue

            precios = [i["precio"] for i in items]
            mediano = statistics.median(precios)

            # Ordenar por precio para analizar los más baratos primero
            items_ordenados = sorted(items, key=lambda x: x["precio"])

            for item in items_ordenados:
                precio     = item["precio"]
                listing_id = item["id"]
                float_val  = item["float"]
                rareza     = item["rareza"]
                is_st      = item["is_st"]
                url_skin   = f"https://csfloat.com/item/{listing_id}"

                # Solo alertar si el precio es MENOR que el mediano
                if precio >= mediano:
                    break  # Los siguientes serán más caros, no tiene sentido seguir

                descuento = ((mediano - precio) / mediano) * 100

                if descuento >= DESCUENTO_MINIMO and listing_id not in alertas_enviadas:
                    alertas_enviadas.add(listing_id)
                    total_alertas += 1
                    embed = build_embed(
                        nombre, precio, mediano, descuento,
                        float_val, rareza, is_st, url_skin, len(items)
                    )
                    await canal.send(embed=embed)
                    print(f"[ALERTA] {nombre} — ${precio:.2f} ({descuento:.1f}% bajo mediano ${mediano:.2f})")

        except Exception as e:
            print(f"[WARN] Error procesando {nombre}: {e}")
            continue

    print(f"Revisados {len(listings)} listings ({len(grupos)} skins unicos).")


# ============================================================
# 🤖 EVENTOS Y COMANDOS
# ============================================================

@bot.event
async def on_ready():
    print(f"[OK] Bot conectado como {bot.user}")
    monitorear.start()
    canal = bot.get_channel(CANAL_ALERTAS_ID)
    if canal:
        embed = discord.Embed(
            title="✅ CSFloat Alert Bot iniciado",
            description=(
                f"Descuento minimo vs mediano CSFloat: **{DESCUENTO_MINIMO}%**\n"
                f"Minimo listings por skin: **{MIN_LISTINGS_PARA_MEDIANO}**\n"
                f"Intervalo: cada **{INTERVALO_MINUTOS} minuto(s)**"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        await canal.send(embed=embed)


@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"🏓 Pong! Latencia: `{round(bot.latency * 1000)}ms`")


@bot.command(name="estado")
async def estado(ctx):
    uptime = datetime.now() - inicio_bot
    horas, resto = divmod(int(uptime.total_seconds()), 3600)
    minutos, segundos = divmod(resto, 60)
    embed = discord.Embed(title="📊 Estado del Bot", color=discord.Color.blurple())
    embed.add_field(name="Estado", value="⏸️ Pausado" if bot_pausado else "▶️ Activo", inline=True)
    embed.add_field(name="Uptime", value=f"{horas}h {minutos}m {segundos}s", inline=True)
    embed.add_field(name="Alertas enviadas", value=str(total_alertas), inline=True)
    embed.add_field(name="Descuento minimo", value=f"{DESCUENTO_MINIMO}%", inline=True)
    embed.add_field(name="Min listings", value=str(MIN_LISTINGS_PARA_MEDIANO), inline=True)
    embed.add_field(name="Intervalo", value=f"{INTERVALO_MINUTOS} min", inline=True)
    await ctx.send(embed=embed)


@bot.command(name="umbral")
async def umbral(ctx, nuevo: int):
    global DESCUENTO_MINIMO
    if nuevo < 1 or nuevo > 90:
        await ctx.send("❌ El umbral debe estar entre 1 y 90.")
        return
    DESCUENTO_MINIMO = nuevo
    await ctx.send(f"✅ Umbral actualizado a **{DESCUENTO_MINIMO}%**")


@bot.command(name="pausa")
async def pausa(ctx):
    global bot_pausado
    bot_pausado = True
    await ctx.send("⏸️ Monitoreo pausado.")


@bot.command(name="reanudar")
async def reanudar(ctx):
    global bot_pausado
    bot_pausado = False
    await ctx.send("▶️ Monitoreo reanudado.")


# ============================================================
# 🚀 INICIO
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  🎮 CSFloat Discord Alert Bot")
    print(f"  Descuento minimo : {DESCUENTO_MINIMO}%")
    print(f"  Intervalo        : cada {INTERVALO_MINUTOS} minutos")
    print("=" * 55)
    bot.run(DISCORD_TOKEN)
