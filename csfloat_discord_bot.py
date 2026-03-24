"""
CSFloat Alert Bot para Discord
================================
Busca por mejores ofertas (best_deal), solo comprar ahora,
rango $10-$200, sin stickers, y compara el 1ro vs 2do listing.
Si la diferencia es X% o mas alerta en Discord.
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

# Diferencia minima entre 1ro y 2do listing para alertar
DIFERENCIA_MINIMA = 10     # %

# Rango de precios en dolares
PRECIO_MIN_USD = 10
PRECIO_MAX_USD = 200

INTERVALO_MINUTOS = 1

# ============================================================

CSFLOAT_BASE     = "https://csfloat.com/api/v1"
alertas_enviadas = set()
bot_pausado      = False
total_alertas    = 0
inicio_bot       = datetime.now()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# 🔧 FUNCIONES DE CSFLOAT
# ============================================================

def tiene_stickers(item):
    """Retorna True si el skin tiene stickers aplicados."""
    stickers = item.get("item", {}).get("stickers", [])
    return len(stickers) > 0


def get_listings():
    """Obtiene hasta 500 listings activos de CSFloat."""
    url     = f"{CSFLOAT_BASE}/listings"
    headers = {
        "Authorization": CSFLOAT_API_KEY,
        "Content-Type":  "application/json",
        "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept":        "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    todos = []

    print(f"[DEBUG] API Key: '{CSFLOAT_API_KEY[:6] if CSFLOAT_API_KEY else 'VACIA'}'...")

    for page in range(10):
        params = {
            "type":      "buy_now",
            "limit":     50,
            "sort_by":   "best_deal",
            "page":      page,
            "min_price": int(PRECIO_MIN_USD * 100),
            "max_price": int(PRECIO_MAX_USD * 100),
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"[DEBUG] Pagina {page} — Status: {resp.status_code}")
            if resp.status_code != 200:
                print(f"[DEBUG] Respuesta: {resp.text[:300]}")
                break
            data  = resp.json()
            items = data.get("data", data.get("listings", [])) if isinstance(data, dict) else data
            if not items:
                print(f"[DEBUG] Pagina {page} vacia, deteniendo.")
                break
            todos.extend(items)
        except Exception as e:
            print(f"[ERROR] Pagina {page}: {e}")
            break

    print(f"[DEBUG] Total listings obtenidos: {len(todos)}")
    return todos


def agrupar_por_skin(listings):
    """
    Agrupa listings por nombre exacto de skin.
    Ignora skins con stickers.
    """
    grupos    = defaultdict(list)
    ignorados = 0

    for item in listings:
        try:
            if tiene_stickers(item):
                ignorados += 1
                continue

            nombre     = item["item"].get("market_hash_name", "Desconocido")
            precio     = item["price"] / 100
            float_val  = item["item"].get("float_value", 0)
            listing_id = item["id"]
            is_st      = item["item"].get("is_stattrak", False)
            rareza     = item["item"].get("rarity_name", "")
            wear       = item["item"].get("wear_name", "")

            grupos[nombre].append({
                "precio": precio,
                "id":     listing_id,
                "float":  float_val,
                "rareza": rareza,
                "is_st":  is_st,
                "wear":   wear,
            })
        except (KeyError, TypeError):
            continue

    print(f"[DEBUG] Ignorados con stickers: {ignorados} | Skins unicos: {len(grupos)}")
    return grupos


def build_embed(nombre, precio_1, precio_2, diferencia, float_val, wear, rareza, is_st, url_skin):
    st_tag = "StatTrak™ " if is_st else ""
    if diferencia >= 30:
        color = discord.Color.red()
    elif diferencia >= 20:
        color = discord.Color.orange()
    else:
        color = discord.Color.green()

    embed = discord.Embed(
        title=f"🔥 {st_tag}{nombre}",
        url=url_skin,
        description=f"El listing mas barato es **{diferencia:.1f}% mas barato** que el segundo",
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="💰 Precio (1ro)", value=f"**${precio_1:.2f}**",   inline=True)
    embed.add_field(name="📋 Precio (2do)", value=f"${precio_2:.2f}",       inline=True)
    embed.add_field(name="📉 Diferencia",   value=f"**{diferencia:.1f}%**", inline=True)
    embed.add_field(name="🔢 Float",        value=f"`{float_val:.6f}`",     inline=True)
    embed.add_field(name="👕 Desgaste",     value=wear or "N/A",            inline=True)
    embed.add_field(name="💎 Rareza",       value=rareza or "N/A",          inline=True)
    embed.add_field(name="🔗 Link",         value=f"[Ver en CSFloat]({url_skin})", inline=True)
    embed.set_footer(text="CSFloat Alert Bot • Sin stickers • Comprar ahora")
    return embed


# ============================================================
# ⏱️ TAREA PERIÓDICA
# ============================================================

@tasks.loop(minutes=INTERVALO_MINUTOS)
async def monitorear():
    global total_alertas, bot_pausado

    if bot_pausado:
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Revisando mercado...")
    canal = bot.get_channel(CANAL_ALERTAS_ID)
    if canal is None:
        print("[ERROR] No se encontro el canal.")
        return

    listings = get_listings()
    if not listings:
        print("No se obtuvieron listings.")
        return

    grupos = agrupar_por_skin(listings)
    alertas_esta_ronda = 0

    for nombre, items in grupos.items():
        try:
            if len(items) < 2:
                continue

            # Ordenar por precio dentro del grupo de menor a mayor
            items_ordenados = sorted(items, key=lambda x: x["precio"])
            primero = items_ordenados[0]
            segundo = items_ordenados[1]

            precio_1 = primero["precio"]
            precio_2 = segundo["precio"]

            if precio_1 >= precio_2:
                continue

            diferencia = ((precio_2 - precio_1) / precio_2) * 100

            if diferencia >= DIFERENCIA_MINIMA and primero["id"] not in alertas_enviadas:
                alertas_enviadas.add(primero["id"])
                total_alertas      += 1
                alertas_esta_ronda += 1
                url_skin = f"https://csfloat.com/item/{primero['id']}"

                embed = build_embed(
                    nombre,
                    precio_1, precio_2, diferencia,
                    primero["float"], primero["wear"],
                    primero["rareza"], primero["is_st"],
                    url_skin
                )
                await canal.send(embed=embed)
                print(f"[ALERTA] {nombre} — ${precio_1:.2f} vs ${precio_2:.2f} ({diferencia:.1f}%)")

        except Exception as e:
            print(f"[WARN] Error procesando {nombre}: {e}")
            continue

    print(f"Revisados {len(listings)} listings | {len(grupos)} skins sin stickers | {alertas_esta_ronda} alertas esta ronda.")


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
                f"Modo: **Mejores ofertas • Solo comprar ahora**\n"
                f"Solo skins **sin stickers**\n"
                f"Rango de precios: **${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}**\n"
                f"Diferencia minima 1ro vs 2do: **{DIFERENCIA_MINIMA}%**\n"
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
    embed.add_field(name="Estado",          value="⏸️ Pausado" if bot_pausado else "▶️ Activo", inline=True)
    embed.add_field(name="Uptime",          value=f"{horas}h {minutos}m {segundos}s",           inline=True)
    embed.add_field(name="Alertas totales", value=str(total_alertas),                            inline=True)
    embed.add_field(name="Diferencia min",  value=f"{DIFERENCIA_MINIMA}%",                       inline=True)
    embed.add_field(name="Rango precios",   value=f"${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}",      inline=True)
    embed.add_field(name="Intervalo",       value=f"{INTERVALO_MINUTOS} min",                    inline=True)
    await ctx.send(embed=embed)


@bot.command(name="umbral")
async def umbral(ctx, nuevo: int):
    global DIFERENCIA_MINIMA
    if nuevo < 1 or nuevo > 90:
        await ctx.send("❌ El umbral debe estar entre 1 y 90.")
        return
    DIFERENCIA_MINIMA = nuevo
    await ctx.send(f"✅ Diferencia minima actualizada a **{DIFERENCIA_MINIMA}%**")


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
    print(f"  Modo          : Mejores ofertas (best_deal)")
    print(f"  Tipo          : Solo comprar ahora")
    print(f"  Rango         : ${PRECIO_MIN_USD} - ${PRECIO_MAX_USD}")
    print(f"  Diferencia min: {DIFERENCIA_MINIMA}%")
    print(f"  Stickers      : Ignorados")
    print(f"  Intervalo     : cada {INTERVALO_MINUTOS} minutos")
    print("=" * 55)
    bot.run(DISCORD_TOKEN)
