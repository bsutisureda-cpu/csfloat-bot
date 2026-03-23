"""
CSFloat Alert Bot para Discord
================================
Monitorea listings en CSFloat y te avisa cuando aparece un skin
con precio X% por debajo del promedio del mercado o del segundo listing.
"""

import requests
import os
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

DESCUENTO_MINIMO     = 15   # % vs promedio histórico
DESCUENTO_VS_SEGUNDO = 10   # % vs segundo listing
PRECIO_MIN_USD       = 10
PRECIO_MAX_USD       = 500
INTERVALO_MINUTOS    = 1

# ============================================================

CSFLOAT_BASE = "https://csfloat.com/api/v1"

precios_promedio  = defaultdict(list)
alertas_enviadas  = set()
bot_pausado       = False
total_alertas     = 0
inicio_bot        = datetime.now()
listings_por_skin = defaultdict(list)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# 🔧 FUNCIONES DE CSFLOAT
# ============================================================

def get_listings():
    """Obtiene listings activos de CSFloat sin autenticación (endpoint público)."""
    url = f"{CSFLOAT_BASE}/listings"
    params = {
        "type": "buy_now",
        "min_price": int(PRECIO_MIN_USD * 100),
        "max_price": int(PRECIO_MAX_USD * 100),
        "page": 0,
        "limit": 50,
        "sort_by": "highest_discount",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        print(f"[DEBUG] Status code: {resp.status_code}")
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"[ERROR] Al obtener listings: {e}")
        return []


def actualizar_promedio(nombre, precio_usd):
    historial = precios_promedio[nombre]
    historial.append(precio_usd)
    if len(historial) > 20:
        historial.pop(0)


def calcular_promedio(nombre):
    h = precios_promedio[nombre]
    if len(h) < 3:
        return None
    return sum(h) / len(h)


def build_embed(nombre, precio, referencia, descuento, float_val, rareza, is_st, url_skin, precio_2=None, metodo="vs promedio"):
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
        description=f"Skin detectado con **{descuento:.1f}% de descuento** ({metodo})",
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="💰 Precio (1ro)", value=f"**${precio:.2f}**", inline=True)
    if precio_2:
        embed.add_field(name="📋 Precio (2do)", value=f"${precio_2:.2f}", inline=True)
    embed.add_field(name="📊 Referencia", value=f"${referencia:.2f}", inline=True)
    embed.add_field(name="📉 Descuento", value=f"**{descuento:.1f}%**", inline=True)
    embed.add_field(name="🔢 Float", value=f"`{float_val:.6f}`", inline=True)
    embed.add_field(name="💎 Rareza", value=rareza or "N/A", inline=True)
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
        print("[ERROR] No se encontró el canal.")
        return

    listings = get_listings()
    if not listings:
        print("No se obtuvieron listings.")
        return

    listings_por_skin.clear()
    for item in listings:
        try:
            nombre     = item["item"].get("market_hash_name", "Desconocido")
            precio     = item["price"] / 100
            float_val  = item["item"].get("float_value", 0)
            listing_id = item["id"]
            is_st      = item["item"].get("is_stattrak", False)
            rareza     = item["item"].get("rarity_name", "")
            listings_por_skin[nombre].append((precio, listing_id, float_val, rareza, is_st))
            actualizar_promedio(nombre, precio)
        except (KeyError, TypeError):
            continue

    for nombre, items in listings_por_skin.items():
        try:
            if len(items) < 2:
                continue

            precio_1, id_1, float_1, rareza_1, is_st_1 = items[0]
            precio_2, _, _, _, _ = items[1]
            url_skin = f"https://csfloat.com/item/{id_1}"

            diff_segundo  = ((precio_2 - precio_1) / precio_2) * 100
            promedio      = calcular_promedio(nombre)
            diff_promedio = ((promedio - precio_1) / promedio) * 100 if promedio else 0

            es_ganga_vs_segundo  = diff_segundo  >= DESCUENTO_VS_SEGUNDO
            es_ganga_vs_promedio = diff_promedio >= DESCUENTO_MINIMO

            if (es_ganga_vs_segundo or es_ganga_vs_promedio) and id_1 not in alertas_enviadas:
                alertas_enviadas.add(id_1)
                total_alertas += 1

                descuento_mostrar = max(diff_segundo, diff_promedio)
                ref_mostrar       = precio_2 if diff_segundo >= diff_promedio else (promedio or precio_2)
                metodo            = "vs 2do listing" if diff_segundo >= diff_promedio else "vs promedio"

                embed = build_embed(
                    nombre, precio_1, ref_mostrar, descuento_mostrar,
                    float_1, rareza_1, is_st_1, url_skin,
                    precio_2=precio_2, metodo=metodo
                )
                await canal.send(embed=embed)
                print(f"[ALERTA] {nombre} — ${precio_1:.2f} ({descuento_mostrar:.1f}% off {metodo})")

        except (KeyError, TypeError, ZeroDivisionError) as e:
            print(f"[WARN] Error procesando {nombre}: {e}")
            continue

    print(f"Revisados {len(listings)} listings ({len(listings_por_skin)} skins únicos).")


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
                f"Descuento mínimo vs promedio: **{DESCUENTO_MINIMO}%**\n"
                f"Descuento mínimo vs 2do listing: **{DESCUENTO_VS_SEGUNDO}%**\n"
                f"Rango de precios: **${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}**\n"
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
    embed.add_field(name="Descuento vs promedio", value=f"{DESCUENTO_MINIMO}%", inline=True)
    embed.add_field(name="Descuento vs 2do", value=f"{DESCUENTO_VS_SEGUNDO}%", inline=True)
    embed.add_field(name="Rango precios", value=f"${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}", inline=True)
    embed.add_field(name="Intervalo", value=f"{INTERVALO_MINUTOS} min", inline=True)
    embed.add_field(name="Skins en memoria", value=str(len(precios_promedio)), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="umbral")
async def umbral(ctx, nuevo: int):
    global DESCUENTO_MINIMO
    if nuevo < 1 or nuevo > 90:
        await ctx.send("❌ El umbral debe estar entre 1 y 90.")
        return
    DESCUENTO_MINIMO = nuevo
    await ctx.send(f"✅ Umbral vs promedio actualizado a **{DESCUENTO_MINIMO}%**")


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


@bot.command(name="promedio")
async def promedio(ctx):
    if not precios_promedio:
        await ctx.send("📭 Todavía no hay datos acumulados.")
        return
    top = sorted(precios_promedio.items(), key=lambda x: len(x[1]), reverse=True)[:5]
    embed = discord.Embed(title="📈 Top skins con más datos", color=discord.Color.gold())
    for nombre, historial in top:
        prom = sum(historial) / len(historial)
        embed.add_field(
            name=nombre[:40],
            value=f"Promedio: **${prom:.2f}** ({len(historial)} muestras)",
            inline=False
        )
    await ctx.send(embed=embed)


# ============================================================
# 🚀 INICIO
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  🎮 CSFloat Discord Alert Bot")
    print(f"  Descuento vs promedio  : {DESCUENTO_MINIMO}%")
    print(f"  Descuento vs 2do       : {DESCUENTO_VS_SEGUNDO}%")
    print(f"  Rango de precios       : ${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}")
    print(f"  Intervalo              : cada {INTERVALO_MINUTOS} minutos")
    print("=" * 55)
    bot.run(DISCORD_TOKEN)
