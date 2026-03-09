import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date
import telebot
import time
import qrcode
from io import BytesIO
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- 1. КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Склад Pro Cloud", layout="wide", page_icon="📦")

SCHEMAS = {
    "batches": ["id", "product_name", "batch_number", "expiry_date"],
    "transactions": ["id", "batch_id", "type", "quantity", "buyer", "date", "month", "year"],
    "products": ["name"],
    "clients": ["name"]
}

DEFAULT_URL = "https://docs.google.com/spreadsheets/d/1d7YQfD2Ucv1FLWDJY_Qkd7b0-0H_Akro6eVl6x4NDSk/edit?usp=sharing"
DEFAULT_TOKEN = "8538139467:AAF6xq3ezQnTDt32OPAfi68Z7r3ZZMx2LVc"
DEFAULT_CHAT_ID = "5974057865"

st.sidebar.title("⚙️ Настройки")
SPREADSHEET_URL = st.sidebar.text_input("URL Таблицы", value=DEFAULT_URL)
TELEGRAM_TOKEN = st.sidebar.text_input("Bot Token", value=DEFAULT_TOKEN, type="password")
TELEGRAM_CHAT_ID = st.sidebar.text_input("Chat ID", value=DEFAULT_CHAT_ID)

bot = None
if TELEGRAM_TOKEN:
    try: bot = telebot.TeleBot(TELEGRAM_TOKEN)
    except: pass

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"Ошибка подключения: {e}"); st.stop()

if 'cart' not in st.session_state:
    st.session_state.cart = []

# --- 2. ФУНКЦИИ ---

def get_data(sheet_name):
    try:
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, ttl=0)
        return df.dropna(how='all')
    except:
        return pd.DataFrame(columns=SCHEMAS.get(sheet_name, []))

def safe_update(sheet_name, df):
    try:
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}"); return False

def get_inventory():
    df_b = get_data("batches")
    df_t = get_data("transactions")
    if df_b.empty: return pd.DataFrame()
    
    df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
    if not df_t.empty:
        df_t['quantity'] = pd.to_numeric(df_t['quantity'], errors='coerce').fillna(0)
        df_t['batch_id'] = pd.to_numeric(df_t['batch_id'], errors='coerce')
        df_t['calc_qty'] = df_t.apply(lambda x: x['quantity'] if x['type'] == 'IN' else -x['quantity'], axis=1)
        balance = df_t.groupby('batch_id')['calc_qty'].sum().reset_index()
        inventory = pd.merge(df_b, balance, left_on='id', right_on='batch_id', how='left')
        inventory['calc_qty'] = inventory['calc_qty'].fillna(0).round(2)
    else:
        inventory = df_b.copy()
        inventory['calc_qty'] = 0.0
    
    return inventory.rename(columns={'product_name': 'Товар', 'batch_number': 'Партия', 'expiry_date': 'Срок годности', 'calc_qty': 'Остаток'})

def generate_qr(data_text):
    # Генерируем QR только с ID (числом) для стабильной работы сканера
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(str(data_text))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def read_qr_from_image(image_file):
    try:
        img = Image.open(image_file)
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img_cv)
        return data
    except: return None

def generate_print_sheet(df_selected):
    """Создание листа с QR-кодами только для выбранных строк"""
    qr_w, qr_h = 250, 250
    padding = 60
    cols = 3
    rows = (len(df_selected) + cols - 1) // cols
    
    sheet_w = (qr_w + padding) * cols + padding
    sheet_h = (qr_h + padding + 100) * rows + padding
    
    canvas = Image.new('RGB', (sheet_w, sheet_h), 'white')
    draw = ImageDraw.Draw(canvas)
    
    for i, (_, row) in enumerate(df_selected.iterrows()):
        # В QR пишем только чистый ID
        clean_id = str(row['id']).replace('.0', '')
        qr_bits = generate_qr(clean_id)
        qr_img = Image.open(BytesIO(qr_bits)).resize((qr_w, qr_h))
        
        c, r = i % cols, i // cols
        x = padding + c * (qr_w + padding)
        y = padding + r * (qr_h + padding + 100)
        
        canvas.paste(qr_img, (x, y))
        
        # Подпись под кодом
        info_text = f"ID: {clean_id}\n{row['Товар'][:20]}\nПартия: {row['Партия'][:15]}\nДо: {row['Срок годности']}"
        draw.text((x, y + qr_h + 5), info_text, fill="black")
        
    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()

# --- 3. ИНТЕРФЕЙС ---
menu = ["📊 Склад", "📥 Приход", "📤 Расход (Продажа)", "📈 Аналитика"]
choice = st.sidebar.radio("Навигация", menu)

if choice == "📊 Склад":
    st.header("📦 Текущие остатки")
    df = get_inventory()
    if not df.empty:
        # Интерактивная таблица с выбором строк
        st.subheader("Выберите товары для печати QR-кодов:")
        # Добавляем колонку для выбора
        df_for_select = df.copy()
        df_for_select.insert(0, "Выбор", False)
        
        edited_df = st.data_editor(
            df_for_select[['Выбор', 'id', 'Товар', 'Партия', 'Срок годности', 'Остаток']],
            hide_index=True,
            column_config={"Выбор": st.column_config.CheckboxColumn(required=True)},
            use_container_width=True
        )
        
        selected_rows = edited_df[edited_df["Выбор"] == True]
        
        if not selected_rows.empty:
            st.write(f"Выбрано товаров для печати: {len(selected_rows)}")
            if st.button("📄 Сгенерировать лист QR-кодов для выбранных"):
                with st.spinner("Создаем лист..."):
                    sheet = generate_print_sheet(selected_rows)
                    st.image(sheet)
                    st.download_button("📥 Скачать лист (PNG)", data=sheet, file_name=f"labels_{date.today()}.png")
        else:
            st.info("Отметьте галочками товары в таблице, чтобы напечатать их QR-коды.")
    else:
        st.info("Склад пуст")

elif choice == "📥 Приход":
    st.header("🚚 Приход товара")
    df_prod = get_data("products")
    with st.form("in_form", clear_on_submit=True):
        p_sel = st.selectbox("Товар", ["+ Новый..."] + (df_prod['name'].tolist() if not df_prod.empty else []))
        p_new = st.text_input("Название (если новый)")
        col1, col2 = st.columns(2)
        b_num = col1.text_input("Партия")
        qty = col1.number_input("Кол-во", min_value=0.01, step=0.01, format="%.2f")
        exp = col2.date_input("Годен до")
        
        if st.form_submit_button("✅ Принять"):
            name = p_new if p_sel == "+ Новый..." else p_sel
            if name and b_num:
                if p_sel == "+ Новый...": safe_update("products", pd.concat([df_prod, pd.DataFrame([{"name": name}])]))
                df_b = get_data("batches")
                new_id = 1 if df_b.empty else int(pd.to_numeric(df_b['id']).max()) + 1
                safe_update("batches", pd.concat([df_b, pd.DataFrame([{"id": new_id, "product_name": name, "batch_number": b_num, "expiry_date": str(exp)}])]))
                df_t = get_data("transactions")
                safe_update("transactions", pd.concat([df_t, pd.DataFrame([{"id": len(df_t)+1, "batch_id": new_id, "type": "IN", "quantity": round(qty, 2), "buyer": "СКЛАД", "date": str(date.today()), "month": date.today().month, "year": date.today().year}])]))
                st.success(f"Добавлено: {name}"); time.sleep(1); st.rerun()

elif choice == "📤 Расход (Продажа)":
    st.header("💸 Оформление продажи")
    df_inv = get_inventory()
    df_avail = df_inv[df_inv['Остаток'] > 0] if not df_inv.empty else pd.DataFrame()
    
    # Выбор клиента
    df_clients = get_data("clients")
    c_sel = st.selectbox("Клиент", ["+ Новый..."] + (df_clients['name'].tolist() if not df_clients.empty else []))
    c_new = st.text_input("Имя нового клиента") if c_sel == "+ Новый..." else None
    client_name = c_new if c_sel == "+ Новый..." else c_sel

    t1, t2 = st.tabs(["📷 Сканер QR", "🔎 Поиск из списка"])
    scanned_id = None
    
    with t1:
        img = st.camera_input("Сканировать код мобильным")
        if img:
            raw_data = read_qr_from_image(img)
            if raw_data:
                try: 
                    # Приводим к чистому целому числу (удаляем .0)
                    scanned_id = int(float(str(raw_data).strip()))
                    st.success(f"Распознан товар ID: {scanned_id}")
                except: st.error(f"Некорректный код: {raw_data}")
    
    with t2:
        if not df_avail.empty:
            items_list = df_avail.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']}) | Ост: {x['Остаток']:.2f}", axis=1).tolist()
            manual_sel = st.selectbox("Выберите вручную", ["Не выбрано"] + items_list)
            if manual_sel != "Не выбрано":
                scanned_id = int(float(manual_sel.split("|")[0].replace("ID:", "")))

    if scanned_id:
        match = df_avail[df_avail['id'] == scanned_id]
        if not match.empty:
            row = match.iloc[0]
            st.info(f"Выбрано: {row['Товар']} (Доступно: {row['Остаток']:.2f})")
            qty_out = st.number_input("Кол-во к продаже", min_value=0.01, max_value=float(row['Остаток']), step=0.01)
            if st.button("➕ Добавить в корзину"):
                st.session_state.cart.append({"batch_id": scanned_id, "name": row['Товар'], "qty": round(qty_out, 2)})
                st.rerun()
        else:
            st.error(f"ID {scanned_id} не найден или закончился на складе")

    if st.session_state.cart:
        st.markdown("---")
        st.subheader(f"🛒 Корзина: {client_name}")
        cart_df = pd.DataFrame(st.session_state.cart)
        st.table(cart_df[['name', 'qty']].rename(columns={'name': 'Товар', 'qty': 'Кол-во'}))
        
        col_ok, col_del = st.columns(2)
        if col_ok.button("✅ Провести продажу", type="primary"):
            if not client_name: st.error("Укажите клиента!"); st.stop()
            if c_sel == "+ Новый...": safe_update("clients", pd.concat([df_clients, pd.DataFrame([{"name": client_name}])]))
            
            df_t = get_data("transactions")
            t_id = int(df_t['id'].max() if not df_t.empty else 0) + 1
            new_recs = []
            for item in st.session_state.cart:
                new_recs.append({"id": t_id, "batch_id": item['batch_id'], "type": "OUT", "quantity": item['qty'], "buyer": client_name, "date": str(date.today()), "month": date.today().month, "year": date.today().year})
                t_id += 1
            
            if safe_update("transactions", pd.concat([df_t, pd.DataFrame(new_recs)])):
                st.success("Продажа успешно проведена!"); st.session_state.cart = []; time.sleep(1); st.rerun()
        if col_del.button("🗑 Очистить корзину"): st.session_state.cart = []; st.rerun()

elif choice == "📈 Аналитика":
    st.header("📈 История и Аналитика")
    df_t = get_data("transactions")
    if not df_t.empty:
        st.subheader("Последние операции")
        st.dataframe(df_t.sort_values('id', ascending=False).head(20), use_container_width=True)
