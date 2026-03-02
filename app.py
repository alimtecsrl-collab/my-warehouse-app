import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date, timedelta
import telebot
import time

# --- 1. КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Склад Pro Cloud", layout="wide", page_icon="📦")

# Цветовая схема и стили CSS
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; }
    .stDataFrame { border: 1px solid #f0f2f6; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

# Структура таблиц
SCHEMAS = {
    "batches": ["id", "product_name", "batch_number", "expiry_date"],
    "transactions": ["id", "batch_id", "type", "quantity", "buyer", "date", "month", "year"],
    "products": ["name"],
    "clients": ["name"]
}

# Данные по умолчанию (ваши)
DEFAULT_URL = "https://docs.google.com/spreadsheets/d/1d7YQfD2Ucv1FLWDJY_Qkd7b0-0H_Akro6eVl6x4NDSk/edit?usp=sharing"
DEFAULT_TOKEN = "8538139467:AAF6xq3ezQnTDt32OPAfi68Z7r3ZZMx2LVc"
DEFAULT_CHAT_ID = "5974057865"

# Сайдбар
st.sidebar.title("⚙️ Настройки")
SPREADSHEET_URL = st.sidebar.text_input("URL Таблицы", value=DEFAULT_URL)
TELEGRAM_TOKEN = st.sidebar.text_input("Bot Token", value=DEFAULT_TOKEN, type="password")
TELEGRAM_CHAT_ID = st.sidebar.text_input("Chat ID", value=DEFAULT_CHAT_ID)

# Инициализация бота
bot = None
if TELEGRAM_TOKEN:
    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
    except Exception: pass

# Подключение к Google Sheets
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"Ошибка подключения к GSheets: {e}")
    st.stop()

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_data(sheet_name):
    """Безопасное чтение данных"""
    try:
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, ttl=0)
        df = df.dropna(how='all')
        if df.empty or not set(SCHEMAS[sheet_name]).issubset(df.columns):
            return pd.DataFrame(columns=SCHEMAS[sheet_name])
        return df
    except Exception:
        return pd.DataFrame(columns=SCHEMAS.get(sheet_name, []))

def safe_update(sheet_name, df):
    """Обновление данных в облаке"""
    try:
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

def get_inventory():
    """Расчет текущих остатков"""
    df_b = get_data("batches")
    df_t = get_data("transactions")
    
    if df_b.empty: 
        return pd.DataFrame(columns=['id', 'Товар', 'Партия', 'Срок годности', 'Остаток'])
    
    # Принудительно конвертируем ID в числа для корректного слияния
    df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
    
    if not df_t.empty:
        df_t['quantity'] = pd.to_numeric(df_t['quantity'], errors='coerce').fillna(0)
        df_t['batch_id'] = pd.to_numeric(df_t['batch_id'], errors='coerce')
        
        # Расчет: IN = +, OUT = -
        df_t['calc_qty'] = df_t.apply(lambda x: x['quantity'] if x['type'] == 'IN' else -x['quantity'], axis=1)
        balance = df_t.groupby('batch_id')['calc_qty'].sum().reset_index()
        
        inventory = pd.merge(df_b, balance, left_on='id', right_on='batch_id', how='left')
        inventory['calc_qty'] = inventory['calc_qty'].fillna(0)
    else:
        inventory = df_b.copy()
        inventory['calc_qty'] = 0.0

    return inventory.rename(columns={
        'product_name': 'Товар', 'batch_number': 'Партия', 
        'expiry_date': 'Срок годности', 'calc_qty': 'Остаток'
    })

def style_inventory(row):
    """Улучшенная контрастная стилизация"""
    try:
        exp = pd.to_datetime(row['Срок годности']).date()
        days = (exp - date.today()).days
        # Просрочка: Яркий красный фон, Белый текст
        if days < 0:
            return ['background-color: #d32f2f; color: white; font-weight: bold'] * len(row)
        # 7 дней и меньше: Желтый фон, Черный текст
        if days <= 7:
            return ['background-color: #fbc02d; color: black; font-weight: bold'] * len(row)
    except: pass
    return [''] * len(row)

# --- 3. ИНТЕРФЕЙС ---

menu = ["📊 Склад", "📥 Приход", "📤 Расход", "📈 Аналитика"]
choice = st.sidebar.radio("Навигация", menu)

# --- РАЗДЕЛ: СКЛАД ---
if choice == "📊 Склад":
    st.header("📦 Текущие остатки")
    df = get_inventory()
    
    if not df.empty:
        st.dataframe(
            df[['id', 'Товар', 'Партия', 'Срок годности', 'Остаток']].style.apply(style_inventory, axis=1),
            use_container_width=True, height=450
        )
        
        if st.button("📢 Отправить отчет о сроках в Telegram"):
            # Короткая логика алертов
            today = date.today()
            alerts = []
            for _, r in df.iterrows():
                try:
                    exp = pd.to_datetime(r['Срок годности']).date()
                    if r['Остаток'] > 0 and (exp - today).days <= 7:
                        icon = "🚨" if exp < today else "⚠️"
                        alerts.append(f"{icon} *{r['Товар']}* ({r['Партия']})\nОстаток: {r['Остаток']} | До: {exp}")
                except: continue
            
            if alerts and bot:
                bot.send_message(TELEGRAM_CHAT_ID, "🛑 *ВНИМАНИЕ: СРОКИ!*\n\n" + "\n\n".join(alerts), parse_mode="Markdown")
                st.success("Отчет отправлен!")
            else:
                st.info("Проблемных товаров не найдено.")
    else:
        st.info("На складе пока ничего нет.")

# --- РАЗДЕЛ: ПРИХОД ---
elif choice == "📥 Приход":
    st.header("🚚 Приход товара")
    df_prod = get_data("products")
    options = ["+ Новый товар..."] + (df_prod['name'].tolist() if not df_prod.empty else [])
    
    with st.form("in_form", clear_on_submit=True):
        p_sel = st.selectbox("Товар", options)
        p_new = st.text_input("Название (если новый)")
        col1, col2 = st.columns(2)
        b_num = col1.text_input("Партия")
        qty = col1.number_input("Количество", min_value=0.1, step=1.0, value=1.0)
        exp = col2.date_input("Годен до", min_value=date.today())
        
        if st.form_submit_button("✅ Принять"):
            final_name = p_new if p_sel == "+ Новый товар..." else p_sel
            if final_name and b_num:
                # 1. Товар
                if p_sel == "+ Новый товар...":
                    safe_update("products", pd.concat([df_prod, pd.DataFrame([{"name": final_name}])], ignore_index=True))
                # 2. Партия
                df_b = get_data("batches")
                new_id = 1 if df_b.empty else int(pd.to_numeric(df_b['id']).max()) + 1
                new_batch = pd.DataFrame([{"id": new_id, "product_name": final_name, "batch_number": b_num, "expiry_date": str(exp)}])
                safe_update("batches", pd.concat([df_b, new_batch], ignore_index=True))
                # 3. Транзакция
                df_t = get_data("transactions")
                t_id = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                new_trans = pd.DataFrame([{
                    "id": t_id, "batch_id": new_id, "type": "IN", "quantity": float(qty),
                    "buyer": "СКЛАД", "date": str(date.today()), "month": date.today().month, "year": date.today().year
                }])
                if safe_update("transactions", pd.concat([df_t, new_trans], ignore_index=True)):
                    st.success(f"Принято: {final_name} — {qty} шт.")
                    time.sleep(1)
                    st.rerun()

# --- РАЗДЕЛ: РАСХОД ---
elif choice == "📤 Расход":
    st.header("💸 Расход (Продажа)")
    df_inv = get_inventory()
    df_avail = df_inv[df_inv['Остаток'] > 0] if not df_inv.empty else pd.DataFrame()
    
    if not df_avail.empty:
        df_clients = get_data("clients")
        c_options = ["+ Новый клиент..."] + (df_clients['name'].tolist() if not df_clients.empty else [])
        
        with st.form("out_form", clear_on_submit=True):
            items = df_avail.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']}) | Доступно: {x['Остаток']}", axis=1).tolist()
            sel_item = st.selectbox("Выберите товар", items)
            c_sel = st.selectbox("Клиент", c_options)
            c_new = st.text_input("Имя клиента (если новый)")
            qty_out = st.number_input("Количество к продаже", min_value=0.1, step=1.0, value=1.0)
            
            if st.form_submit_button("🔥 Оформить расход"):
                # Парсинг ID и лимита
                b_id = int(float(sel_item.split("|")[0].replace("ID:", "").strip()))
                max_stock = float(sel_item.split("Доступно:")[1].strip())
                final_client = c_new if c_sel == "+ Новый клиент..." else c_sel
                
                if final_client and 0 < qty_out <= max_stock:
                    if c_sel == "+ Новый клиент...":
                        safe_update("clients", pd.concat([df_clients, pd.DataFrame([{"name": final_client}])], ignore_index=True))
                    
                    df_t = get_data("transactions")
                    t_id = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                    new_sale = pd.DataFrame([{
                        "id": t_id, "batch_id": b_id, "type": "OUT", "quantity": float(qty_out),
                        "buyer": final_client, "date": str(date.today()), 
                        "month": date.today().month, "year": date.today().year
                    }])
                    if safe_update("transactions", pd.concat([df_t, new_sale], ignore_index=True)):
                        st.success(f"Продано {qty_out} шт. ({final_client})")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.error(f"Недостаточно товара! (В наличии: {max_stock})")
    else:
        st.warning("Нет товаров в наличии.")

# --- РАЗДЕЛ: АНАЛИТИКА ---
elif choice == "📈 Аналитика":
    st.header("📊 Аналитика")
    df_t = get_data("transactions")
    df_b = get_data("batches")
    
    if not df_t.empty and not df_b.empty:
        # Принудительная конвертация для объединения
        df_t['batch_id'] = pd.to_numeric(df_t['batch_id'], errors='coerce')
        df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
        
        full = pd.merge(df_t, df_b[['id', 'product_name']], left_on='batch_id', right_on='id', how='left')
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Топ покупателей")
            sales = full[full['type'] == 'OUT']
            if not sales.empty:
                st.bar_chart(sales.groupby('buyer')['quantity'].sum())
        with col2:
            st.subheader("Активность по датам")
            st.line_chart(full.groupby('date')['quantity'].count())
            
        st.subheader("Последние операции")
        st.dataframe(full[['date', 'product_name', 'type', 'quantity', 'buyer']].sort_values(by='date', ascending=False), use_container_width=True)
    else:
        st.info("Данных для анализа пока нет.")