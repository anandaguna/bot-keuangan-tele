import os
import sqlite3
from datetime import datetime, timedelta, timezone
import bcrypt

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

from dotenv import load_dotenv
load_dotenv()

# ========= CONFIG =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum di-set. Isi file .env dengan BOT_TOKEN=... atau set env BOT_TOKEN.")

DB_PATH = "finance_bot.db"

# Timezone Indonesia Tengah (WITA) = UTC+8, cocok Asia/Makassar
WITA = timezone(timedelta(hours=8))

# ========= UTIL =========
def rupiah(n: int) -> str:
    return "Rp." + f"{int(n):,}".replace(",", ".")

# ========= DB =========
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash BLOB NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('IN','OUT')),
        amount INTEGER NOT NULL CHECK(amount >= 0),
        note TEXT,
        ts TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    conn.commit()
    conn.close()

def find_user(username: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row

def create_user(username: str, password: str):
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
        (username, pw_hash, datetime.now(WITA).isoformat())
    )
    conn.commit()
    conn.close()

def verify_user(username: str, password: str) -> bool:
    u = find_user(username)
    if not u:
        return False
    return bcrypt.checkpw(password.encode(), u["password_hash"])

def add_tx(user_id: int, tx_type: str, amount: int, note: str | None):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(user_id, type, amount, note, ts) VALUES(?,?,?,?,?)",
        (user_id, tx_type, amount, note, datetime.now(WITA).isoformat())
    )
    conn.commit()
    conn.close()

def sum_tx(user_id: int, tx_type: str, start_iso: str, end_iso: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE user_id=? AND type=? AND ts>=? AND ts<? 
        """,
        (user_id, tx_type, start_iso, end_iso)
    )
    total = cur.fetchone()["total"]
    conn.close()
    return int(total)

# ========= SESSION =========
def is_logged_in(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return "user_id" in context.user_data

def menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["➕ Uang Masuk", "➖ Uang Keluar"],
            ["📅 Masuk Hari Ini", "🗓️ Keluar Minggu Ini"],
            ["📆 Rekap Bulan Ini", "🚪 Logout"],
        ],
        resize_keyboard=True
    )

# ========= CONVERSATION STATES =========
REG_USERNAME, REG_PASSWORD = range(2)
LOGIN_USERNAME, LOGIN_PASSWORD = range(2, 4)
IN_AMOUNT, IN_NOTE = range(4, 6)
OUT_AMOUNT, OUT_NOTE = range(6, 8)

# ========= HANDLERS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Ini bot pencatatan keuangan.\n"
        "Ketik /register untuk daftar atau /login untuk masuk."
    )

# ---- REGISTER ----
async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Buat username:")
    return REG_USERNAME

async def reg_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if len(username) < 3:
        await update.message.reply_text("Username minimal 3 karakter. Coba lagi:")
        return REG_USERNAME
    if find_user(username):
        await update.message.reply_text("Username sudah dipakai. Coba username lain:")
        return REG_USERNAME
    context.user_data["reg_username"] = username
    await update.message.reply_text("Buat password (minimal 6 karakter):")
    return REG_PASSWORD

async def reg_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if len(password) < 6:
        await update.message.reply_text("Password minimal 6 karakter. Coba lagi:")
        return REG_PASSWORD

    username = context.user_data["reg_username"]
    create_user(username, password)
    context.user_data.pop("reg_username", None)

    await update.message.reply_text("Registrasi berhasil ✅\nSekarang ketik /login untuk masuk.")
    return ConversationHandler.END

# ---- LOGIN ----
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Username:")
    return LOGIN_USERNAME

async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login_username"] = update.message.text.strip()
    await update.message.reply_text("Password:")
    return LOGIN_PASSWORD

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = context.user_data.get("login_username", "")
    password = update.message.text.strip()
    context.user_data.pop("login_username", None)

    if not verify_user(username, password):
        await update.message.reply_text("Login gagal ❌ Username/password salah.\nKetik /login untuk coba lagi.")
        return ConversationHandler.END

    u = find_user(username)
    context.user_data["user_id"] = int(u["id"])
    context.user_data["username"] = username

    await update.message.reply_text(
        f"Login berhasil ✅ Selamat datang, {username}!",
        reply_markup=menu_keyboard()
    )
    return ConversationHandler.END

async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("user_id", None)
    context.user_data.pop("username", None)
    await update.message.reply_text("Kamu sudah logout. Ketik /login untuk masuk lagi.")

# ---- INPUT UANG MASUK ----
async def in_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(context):
        await update.message.reply_text("Kamu belum login. Ketik /login dulu.")
        return ConversationHandler.END
    await update.message.reply_text("Masukkan nominal UANG MASUK (angka saja). Contoh: 50000")
    return IN_AMOUNT

async def in_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(".", "").replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("Nominal harus angka. Coba lagi:")
        return IN_AMOUNT
    context.user_data["tmp_amount"] = int(text)
    await update.message.reply_text("Catatan (boleh kosong, ketik '-' untuk skip):")
    return IN_NOTE

async def in_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    if note == "-":
        note = None
    amount = context.user_data.pop("tmp_amount", 0)
    add_tx(context.user_data["user_id"], "IN", amount, note)
    await update.message.reply_text(f"✅ Uang masuk tercatat: {rupiah(amount)}", reply_markup=menu_keyboard())
    return ConversationHandler.END

# ---- INPUT UANG KELUAR ----
async def out_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(context):
        await update.message.reply_text("Kamu belum login. Ketik /login dulu.")
        return ConversationHandler.END
    await update.message.reply_text("Masukkan nominal UANG KELUAR (angka saja). Contoh: 25000")
    return OUT_AMOUNT

async def out_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(".", "").replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("Nominal harus angka. Coba lagi:")
        return OUT_AMOUNT
    context.user_data["tmp_amount"] = int(text)
    await update.message.reply_text("Catatan (boleh kosong, ketik '-' untuk skip):")
    return OUT_NOTE

async def out_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    if note == "-":
        note = None
    amount = context.user_data.pop("tmp_amount", 0)
    add_tx(context.user_data["user_id"], "OUT", amount, note)
    await update.message.reply_text(f"✅ Uang keluar tercatat: {rupiah(amount)}", reply_markup=menu_keyboard())
    return ConversationHandler.END

# ---- REPORTS ----
def today_range():
    now = datetime.now(WITA)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()

def week_range_monday():
    now = datetime.now(WITA)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start - timedelta(days=start.weekday())  # Monday=0
    end = start + timedelta(days=7)
    return start.isoformat(), end.isoformat()

def month_range():
    now = datetime.now(WITA)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()

async def report_in_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(context):
        await update.message.reply_text("Kamu belum login. Ketik /login dulu.")
        return
    s, e = today_range()
    total = sum_tx(context.user_data["user_id"], "IN", s, e)
    await update.message.reply_text(f"📅 Uang MASUK hari ini: {rupiah(total)}", reply_markup=menu_keyboard())

async def report_out_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(context):
        await update.message.reply_text("Kamu belum login. Ketik /login dulu.")
        return
    s, e = week_range_monday()
    total = sum_tx(context.user_data["user_id"], "OUT", s, e)
    await update.message.reply_text(
        f"🗓️ Uang KELUAR minggu ini (Senin–Minggu): {rupiah(total)}",
        reply_markup=menu_keyboard()
    )

async def report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(context):
        await update.message.reply_text("Kamu belum login. Ketik /login dulu.")
        return
    s, e = month_range()
    masuk = sum_tx(context.user_data["user_id"], "IN", s, e)
    keluar = sum_tx(context.user_data["user_id"], "OUT", s, e)
    saldo = masuk - keluar
    await update.message.reply_text(
        f"📆 Rekap BULAN ini:\n"
        f"- Masuk : {rupiah(masuk)}\n"
        f"- Keluar: {rupiah(keluar)}\n"
        f"- Saldo : {rupiah(saldo)}",
        reply_markup=menu_keyboard()
    )

# ---- MENU BUTTON ROUTER (report + logout) ----
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "📅 Masuk Hari Ini":
        return await report_in_today(update, context)
    if text == "🗓️ Keluar Minggu Ini":
        return await report_out_week(update, context)
    if text == "📆 Rekap Bulan Ini":
        return await report_month(update, context)
    if text == "🚪 Logout":
        return await logout_cmd(update, context)

    return

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("tmp_amount", None)
    context.user_data.pop("reg_username", None)
    context.user_data.pop("login_username", None)
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_cmd)],
        states={
            REG_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_username)],
            REG_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", login_cmd)],
        states={
            LOGIN_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_username)],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    in_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Uang Masuk$"), in_start)],
        states={
            IN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_amount)],
            IN_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, in_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    out_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➖ Uang Keluar$"), out_start)],
        states={
            OUT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, out_amount)],
            OUT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, out_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Urutan penting: conversation dulu
    app.add_handler(reg_conv)
    app.add_handler(login_conv)
    app.add_handler(in_conv)
    app.add_handler(out_conv)

    # Router report + logout
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    app.add_handler(CommandHandler("logout", logout_cmd))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
