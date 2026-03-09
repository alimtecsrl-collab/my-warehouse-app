import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date
import telebot
import time

# Новые библиотеки для QR
import qrcode
from io import BytesIO
import cv2
import numpy as np
from PIL import Image

# --- 1. КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Склад Pro Cloud", layout="wide", page_icon="📦")

st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; }
    .stDataFrame { border: 1px solid #f0f2f6; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

SCHEMAS = {
    "batches": ["id", "product_name", "batch_number", "expiry_date"],
    "transactions": ["id", "batch_id", "type", "quantity", "buyer", "date", "month", "year"],
    "products": ["name"],
    "clients": ["name"]
}

# ВАШИ ДАННЫЕ
DEFAULT_URL = "https://docs.google.com/spreadsheets/d/1d7YQfD2Ucv1FLWDJY_Qkd7b0-0H_Akro6eVl6x4NDSk/edit?usp=sharing"
DEFAULT_TOKEN = "8538139467:AAF6xq3ezQnTDt32OPAfi68Z7r3ZZMx2LVc"
DEFAULT_CHAT_ID = "5974057865"

st.sidebar.title("⚙️ Настройки")
SPREADSHEET_URL = st.sidebar.text_input("URL Таблицы", value=DEFAULT_URL)
TELEGRAM_TOKEN = st.sidebar.text_input("Bot Token", value=DEFAULT_TOKEN, type="password")
TELEGRAM_CHAT_ID = st.sidebar.text_input("Chat ID", value=DEFAULT_CHAT_ID)

bot = None
if TELEGRAM_TOKEN:
    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
    except Exception: pass

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"Ошибка подключения к GSheets: {e}")
    st.stop()

# Инициализация корзины
if 'cart' not in st.session_state:
    st.session_state.cart = []

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_data(sheet_name):
    try:
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, ttl=0)
        df = df.dropna(how='all')
        if df.empty or not set(SCHEMAS[sheet_name]).issubset(df.columns):
            return pd.DataFrame(columns=SCHEMAS[sheet_name])
        return df
    except Exception:
        return pd.DataFrame(columns=SCHEMAS.get(sheet_name, []))

def safe_update(sheet_name, df):
    try:
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

def get_inventory():
    df_b = get_data("batches")
    df_t = get_data("transactions")
    
    if df_b.empty: 
        return pd.DataFrame(columns=['id', 'Товар', 'Партия', 'Срок годности', 'Остаток'])
    
    df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
    
    if not df_t.empty:
        df_t['quantity'] = pd.to_numeric(df_t['quantity'], errors='coerce').fillna(0)
        df_t['batch_id'] = pd.to_numeric(df_t['batch_id'], errors='coerce')
        
        df_t['calc_qty'] = df_t.apply(lambda x: x['quantity'] if x['type'] == 'IN' else -x['quantity'], axis=1)
        balance = df_t.groupby('batch_id')['calc_qty'].sum().reset_index()
        
        inventory = pd.merge(df_b, balance, left_on='id', right_on='batch_id', how='left')
        # Округляем остатки до 2 знаков
        inventory['calc_qty'] = inventory['calc_qty'].fillna(0).round(2)
    else:
        inventory = df_b.copy()
        inventory['calc_qty'] = 0.0

    return inventory.rename(columns={
        'product_name': 'Товар', 'batch_number': 'Партия', 
        'expiry_date': 'Срок годности', 'calc_qty': 'Остаток'
    })

def style_inventory(row):
    try:
        exp = pd.to_datetime(row['Срок годности']).date()
        days = (exp - date.today()).days
        if days < 0: return ['background-color: #d32f2f; color: white; font-weight: bold'] * len(row)
        if days <= 30: return ['background-color: #fbc02d; color: black; font-weight: bold'] * len(row)
    except: pass
    return [''] * len(row)

def generate_qr(data_text):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def read_qr_from_image(image_file):
    try:
        img = Image.open(image_file)
        # Конвертируем в оттенки серого для лучшего распознавания
        img_gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img_gray)
        return data
    except Exception as e:
        return None

# --- 3. ИНТЕРФЕЙС ---
menu = ["📊 Склад", "📥 Приход", "📤 Расход (Продажа)", "📈 Аналитика"]
choice = st.sidebar.radio("Навигация", menu)

# --- РАЗДЕЛ: СКЛАД ---
if choice == "📊 Склад":
    st.header("📦 Текущие остатки")
    df = get_inventory()
    
    if not df.empty:
        st.dataframe(
            df[['id', 'Товар', 'Партия', 'Срок годности', 'Остаток']]
            .style.apply(style_inventory, axis=1)
            .format({"Остаток": "{:.2f}"}),
            use_container_width=True, height=400
        )
        
        # Блок генерации QR-кодов
        with st.expander("🖨️ Сгенерировать QR-код для партии"):
            col1, col2 = st.columns([2, 1])
            with col1:
                qr_options = df.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']})", axis=1).tolist()
                sel_qr = st.selectbox("Выберите партию для печати", qr_options)
            with col2:
                if sel_qr:
                    b_id = sel_qr.split("|")[0].replace("ID:", "").strip()
                    qr_img = generate_qr(b_id)
                    st.image(qr_img, caption=f"QR код для {sel_qr}", width=150)
                    st.download_button("💾 Скачать QR", data=qr_img, file_name=f"QR_{b_id}.png", mime="image/png")

        if st.button("📢 Отправить отчет о сроках в Telegram"):
            today = date.today()
            alerts = []
            for _, r in df.iterrows():
                try:
                    exp = pd.to_datetime(r['Срок годности']).date()
                    if r['Остаток'] > 0 and (exp - today).days <= 30:
                        icon = "🚨" if exp < today else "⚠️"
                        alerts.append(f"{icon} *{r['Товар']}* ({r['Партия']})\nОстаток: {r['Остаток']:.2f} | До: {exp}")
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
                if p_sel == "+ Новый товар...":
                    safe_update("products", pd.concat([df_prod, pd.DataFrame([{"name": final_name}])], ignore_index=True))
                
                df_b = get_data("batches")
                new_id = 1 if df_b.empty else int(pd.to_numeric(df_b['id']).max()) + 1
                new_batch = pd.DataFrame([{"id": new_id, "product_name": final_name, "batch_number": b_num, "expiry_date": str(exp)}])
                safe_update("batches", pd.concat([df_b, new_batch], ignore_index=True))
                
                df_t = get_data("transactions")
                t_id = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                new_trans = pd.DataFrame([{
                    "id": t_id, "batch_id": new_id, "type": "IN", "quantity": round(float(qty), 2),
                    "buyer": "СКЛАД", "date": str(date.today()), "month": date.today().month, "year": date.today().year
                }])
                if safe_update("transactions", pd.concat([df_t, new_trans], ignore_index=True)):
                    st.success(f"Принято: {final_name} — {qty:.2f} шт.")
                    time.sleep(1)
                    st.rerun()

# --- РАЗДЕЛ: РАСХОД ---
elif choice == "📤 Расход (Продажа)":
    st.header("💸 Оформление продажи")
    
    # Шаг 1: Клиент
    st.subheader("1. Покупатель")
    df_clients = get_data("clients")
    c_options = ["+ Новый клиент..."] + (df_clients['name'].tolist() if not df_clients.empty else [])
    
    col_c1, col_c2 = st.columns(2)
    c_sel = col_c1.selectbox("Выберите клиента", c_options)
    c_new = col_c2.text_input("Имя клиента (если новый)", disabled=(c_sel != "+ Новый клиент..."))
    final_client = c_new if c_sel == "+ Новый клиент..." else c_sel

    st.markdown("---")
    
    # Шаг 2: Выбор товаров (Вручную или Сканером)
    st.subheader("2. Добавление товаров")
    df_inv = get_inventory()
    df_avail = df_inv[df_inv['Остаток'] > 0] if not df_inv.empty else pd.DataFrame()
    
    if not df_avail.empty:
        tab1, tab2 = st.tabs(["🖱️ Выбрать из списка", "📷 Сканировать QR"])
        
        # Переменные для добавления в корзину
        selected_b_id = None
        selected_item_name = None
        selected_max_stock = 0.0

        with tab1:
            items = df_avail.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']}) | Доступно: {x['Остаток']:.2f}", axis=1).tolist()
            sel_item = st.selectbox("Товар на складе", items)
            if sel_item:
                selected_b_id = int(float(sel_item.split("|")[0].replace("ID:", "").strip()))
                selected_item_name = sel_item.split("|")[1].strip()
                selected_max_stock = float(sel_item.split("Доступно:")[1].strip())
        
        with tab2:
            st.info("Наведите камеру на QR-код партии")
            cam_image = st.camera_input("Сканер")
            if cam_image:
                qr_data = read_qr_from_image(cam_image)
                if qr_data:
                    try:
                        scanned_id = int(qr_data)
                        match = df_avail[df_avail['id'] == scanned_id]
                        if not match.empty:
                            row = match.iloc[0]
                            st.success(f"✅ Распознано: {row['Товар']} (Партия: {row['Партия']})")
                            selected_b_id = scanned_id
                            selected_item_name = f"{row['Товар']} ({row['Партия']})"
                            selected_max_stock = float(row['Остаток'])
                        else:
                            st.error(f"❌ Товар с ID {scanned_id} не найден на складе (или остаток 0).")
                            selected_b_id = None
                    except:
                        st.error("❌ Неверный формат QR-кода.")
                        selected_b_id = None
                else:
                    st.warning("QR-код не найден на изображении. Попробуйте поднести ближе.")
        
        # Форма ввода количества (общая для обоих табов)
        if selected_b_id is not None:
            already_in_cart = sum(item['qty'] for item in st.session_state.cart if item['batch_id'] == selected_b_id)
            available_to_add = selected_max_stock - already_in_cart
            
            if available_to_add > 0:
                col_i1, col_i2 = st.columns([2, 1])
                with col_i1:
                    qty_out = st.number_input(f"Количество для {selected_item_name}", min_value=0.1, max_value=max(0.1, available_to_add), step=1.0, value=1.0)
                with col_i2:
                    st.write("")
                    st.write("")
                    if st.button("➕ В корзину", use_container_width=True):
                        st.session_state.cart.append({
                            "batch_id": selected_b_id,
                            "item_name": selected_item_name,
                            "qty": round(float(qty_out), 2)
                        })
                        st.rerun()
            else:
                st.error("Весь доступный остаток этого товара уже добавлен в корзину!")

        # Шаг 3: Корзина
        if st.session_state.cart:
            st.markdown("---")
            st.subheader(f"🛒 Заказ клиента: {final_client if final_client else '⚠️ Клиент не указан'}")
            
            cart_df = pd.DataFrame(st.session_state.cart)
            display_cart = cart_df.groupby(['batch_id', 'item_name'])['qty'].sum().reset_index()
            
            st.dataframe(
                display_cart[['item_name', 'qty']]
                .rename(columns={"item_name": "Товар", "qty": "Количество"})
                .style.format({"Количество": "{:.2f}"}),
                use_container_width=True
            )

            col_b1, col_b2 = st.columns(2)
            with col_b1:
                if st.button("🔥 Провести списание", type="primary", use_container_width=True):
                    if not final_client:
                        st.error("Сначала укажите имя клиента!")
                    else:
                        if c_sel == "+ Новый клиент...":
                            safe_update("clients", pd.concat([df_clients, pd.DataFrame([{"name": final_client}])], ignore_index=True))

                        df_t = get_data("transactions")
                        t_id_start = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                        
                        new_transactions = []
                        for i, row in display_cart.iterrows():
                            new_transactions.append({
                                "id": t_id_start + i,
                                "batch_id": row['batch_id'],
                                "type": "OUT",
                                "quantity": round(float(row['qty']), 2),
                                "buyer": final_client,
                                "date": str(date.today()),
                                "month": date.today().month,
                                "year": date.today().year
                            })
                            
                        if safe_update("transactions", pd.concat([df_t, pd.DataFrame(new_transactions)], ignore_index=True)):
                            st.success(f"Успешно! Списано позиций: {len(new_transactions)}")
                            st.session_state.cart = [] 
                            time.sleep(1.5)
                            st.rerun()
            with col_b2:
                if st.button("🗑 Очистить корзину", use_container_width=True):
                    st.session_state.cart = []
                    st.rerun()
    else:
        st.warning("Нет товаров в наличии.")

# --- РАЗДЕЛ: АНАЛИТИКА ---
elif choice == "📈 Аналитика":
    st.header("📊 Аналитика")
    df_t = get_data("transactions")
    df_b = get_data("batches")
    
    if not df_t.empty and not df_b.empty:
        df_t['batch_id'] = pd.to_numeric(df_t['batch_id'], errors='coerce')
        df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
        full = pd.merge(df_t, df_b[['id', 'product_name']], left_on='batch_id', right_on='id', how='left')
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Топ покупателей (шт)")
            sales = full[full['type'] == 'OUT']
            if not sales.empty:
                st.bar_chart(sales.groupby('buyer')['quantity'].sum())
        with col2:
            st.subheader("Активность по датам")
            st.line_chart(full.groupby('date')['quantity'].count())
            
        st.subheader("Последние операции")
        st.dataframe(
            full[['date', 'product_name', 'type', 'quantity', 'buyer']]
            .sort_values(by='date', ascending=False)
            .style.format({"quantity": "{:.2f}"}), 
            use_container_width=True
        )
    else:
        st.info("Данных для анализа пока нет.")
