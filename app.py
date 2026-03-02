import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date
import telebot
import time
import qrcode
from PIL import Image
import cv2
import numpy as np
from io import BytesIO

# --- 1. КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Склад Pro Cloud", layout="wide", page_icon="📦")

# Стили
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 8px; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# Инициализация session_state для хранения результата сканирования
if 'scanned_batch_id' not in st.session_state:
    st.session_state.scanned_batch_id = None

# --- ПОДКЛЮЧЕНИЕ ---
# ⚠️ Вставь свои данные или используй st.secrets
SPREADSHEET_URL = st.secrets.get("SPREADSHEET_URL", "ТВОЯ_ССЫЛКА")
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "ТВОЙ_ТОКЕН")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "ТВОЙ_CHAT_ID")

bot = None
if TELEGRAM_TOKEN:
    try: bot = telebot.TeleBot(TELEGRAM_TOKEN)
    except: pass

conn = st.connection("gsheets", type=GSheetsConnection)

# --- 2. ФУНКЦИИ ---

def get_data(sheet_name):
    try:
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, ttl=0)
        return df.dropna(how='all')
    except: return pd.DataFrame()

def safe_update(sheet_name, df):
    try:
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

def generate_qr(batch_id, product_name):
    """Генерирует QR код с ID партии"""
    # Мы кодируем строку вида "BATCH:123"
    data = f"BATCH:{batch_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    return img

def decode_qr(image_buffer):
    """Декодирует QR из фото"""
    try:
        # Конвертация в формат для OpenCV
        bytes_data = image_buffer.getvalue()
        cv_img = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
        
        # Детектор QR
        detector = cv2.QRCodeDetector()
        data, bbox, _ = detector.detectAndDecode(cv_img)
        
        if data and "BATCH:" in data:
            return int(data.split(":")[1])
    except Exception as e:
        st.error(f"Ошибка чтения QR: {e}")
    return None

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
        inv = pd.merge(df_b, balance, left_on='id', right_on='batch_id', how='left')
        inv['calc_qty'] = inv['calc_qty'].fillna(0)
    else:
        inv = df_b.copy()
        inv['calc_qty'] = 0.0
        
    return inv.rename(columns={'product_name': 'Товар', 'batch_number': 'Партия', 'expiry_date': 'Срок годности', 'calc_qty': 'Остаток'})

# --- 3. ИНТЕРФЕЙС ---
menu = ["📊 Склад", "📥 Приемка", "📤 Продажа (Сканер)"]
choice = st.sidebar.radio("Меню", menu)

# --- СКЛАД (С ГЕНЕРАЦИЕЙ QR) ---
if choice == "📊 Склад":
    st.title("📦 Остатки и QR-коды")
    df = get_inventory()
    
    if not df.empty:
        # Показываем таблицу
        st.dataframe(df[['id', 'Товар', 'Партия', 'Остаток', 'Срок годности']], use_container_width=True)
        
        st.divider()
        st.subheader("🖨️ Распечатать QR для товара")
        
        # Выбор товара для печати этикетки
        opts = df.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']})", axis=1).tolist()
        sel_qr = st.selectbox("Выберите партию", opts)
        
        if sel_qr:
            b_id = int(sel_qr.split("|")[0].replace("ID:", "").strip())
            p_name = sel_qr.split("|")[1].strip()
            
            # Показываем QR
            col1, col2 = st.columns([1, 4])
            with col1:
                img = generate_qr(b_id, p_name)
                # Конвертация для Streamlit
                buf = BytesIO()
                img.save(buf)
                st.image(buf, caption=f"Batch #{b_id}", width=150)
            with col2:
                st.info(f"Наведите камеру телефона на этот код в разделе 'Продажа', чтобы быстро найти товар: **{p_name}**")

# --- ПРИЕМКА ---
elif choice == "📥 Приемка":
    st.title("🚚 Приход товара")
    # ... (код приемки, как в прошлой версии)
    df_prod = get_data("products")
    p_list = ["+ Новый..."] + (df_prod['name'].tolist() if not df_prod.empty else [])
    
    with st.form("in_form"):
        p_sel = st.selectbox("Товар", p_list)
        p_new = st.text_input("Название (если новый)")
        col1, col2 = st.columns(2)
        b_num = col1.text_input("Партия")
        qty = col1.number_input("Кол-во", 1.0)
        exp = col2.date_input("Годен до")
        
        if st.form_submit_button("Сохранить"):
            final_name = p_new if p_sel == "+ Новый..." else p_sel
            if final_name and b_num:
                if p_sel == "+ Новый...":
                    safe_update("products", pd.concat([df_prod, pd.DataFrame([{"name": final_name}])], ignore_index=True))
                
                df_b = get_data("batches")
                new_id = 1 if df_b.empty else int(pd.to_numeric(df_b['id']).max()) + 1
                new_batch = pd.DataFrame([{"id": new_id, "product_name": final_name, "batch_number": b_num, "expiry_date": str(exp)}])
                safe_update("batches", pd.concat([df_b, new_batch], ignore_index=True))
                
                df_t = get_data("transactions")
                new_t = pd.DataFrame([{"id": 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1, "batch_id": new_id, "type": "IN", "quantity": qty, "buyer": "Склад", "date": str(date.today()), "month": date.today().month, "year": date.today().year}])
                safe_update("transactions", pd.concat([df_t, new_t], ignore_index=True))
                
                # Сразу показываем QR созданной партии
                st.success(f"Создано! Партия #{new_id}")
                qr_img = generate_qr(new_id, final_name)
                buf = BytesIO()
                qr_img.save(buf)
                st.image(buf, caption=f"QR для {final_name}", width=150)

# --- ПРОДАЖА (СО СКАНЕРОМ) ---
elif choice == "📤 Продажа (Сканер)":
    st.title("💸 Продажа")
    
    df_inv = get_inventory()
    df_avail = df_inv[df_inv['Остаток'] > 0] if not df_inv.empty else pd.DataFrame()
    
    # 1. БЛОК СКАНИРОВАНИЯ
    with st.expander("📷 Сканировать QR-код (Нажмите чтобы открыть камеру)", expanded=True):
        img_file = st.camera_input("Наведите камеру на QR товара")
        
        if img_file is not None:
            # Пытаемся распознать
            detected_id = decode_qr(img_file)
            if detected_id:
                # Проверяем, есть ли такой ID на складе
                if detected_id in df_avail['id'].values:
                    st.session_state.scanned_batch_id = detected_id
                    st.success(f"Товар найден! ID партии: {detected_id}")
                else:
                    st.error(f"Товар с ID {detected_id} не найден или закончился.")
            else:
                st.warning("QR-код не распознан. Попробуйте еще раз.")

    # 2. ФОРМА ПРОДАЖИ
    if not df_avail.empty:
        df_clients = get_data("clients")
        c_opts = ["+ Новый..."] + (df_clients['name'].tolist() if not df_clients.empty else [])
        
        # Формируем список. Если был скан, находим нужный индекс
        options = df_avail.apply(lambda x: f"ID:{x['id']} | {x['Товар']} | Остаток: {x['Остаток']}", axis=1).tolist()
        
        # ЛОГИКА АВТОВЫБОРА ПО СКАНИРОВАНИЮ
        index_to_select = 0
        if st.session_state.scanned_batch_id:
            for i, opt in enumerate(options):
                if f"ID:{st.session_state.scanned_batch_id} " in opt:
                    index_to_select = i
                    break
        
        with st.form("sale_form"):
            st.write("### Оформление")
            sel_item = st.selectbox("Товар", options, index=index_to_select)
            
            # Кнопка сброса скана (чтобы можно было выбрать руками другой товар)
            if st.session_state.scanned_batch_id:
                 st.info("💡 Товар выбран автоматически по QR-коду.")

            c_sel = st.selectbox("Клиент", c_opts)
            c_new = st.text_input("Имя клиента (если новый)")
            qty = st.number_input("Количество", 1.0)
            
            if st.form_submit_button("🔥 Продать"):
                # Логика продажи (как раньше)
                b_id = int(sel_item.split("|")[0].replace("ID:", "").strip())
                max_qty = float(sel_item.split("Остаток:")[1].strip())
                final_client = c_new if c_sel == "+ Новый..." else c_sel
                
                if qty <= max_qty and final_client:
                    if c_sel == "+ Новый...":
                        safe_update("clients", pd.concat([df_clients, pd.DataFrame([{"name": final_client}])], ignore_index=True))
                        
                    df_t = get_data("transactions")
                    new_t = pd.DataFrame([{"id": 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1, "batch_id": b_id, "type": "OUT", "quantity": qty, "buyer": final_client, "date": str(date.today()), "month": date.today().month, "year": date.today().year}])
                    safe_update("transactions", pd.concat([df_t, new_t], ignore_index=True))
                    
                    st.session_state.scanned_batch_id = None # Сброс скана
                    st.success("Продано!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Ошибка (кол-во или имя клиента)")