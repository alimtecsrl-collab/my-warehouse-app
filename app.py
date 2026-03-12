import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date, timedelta
import telebot
import time
import qrcode
from io import BytesIO
import cv2
import numpy as np
from PIL import Image, ImageDraw

# --- 1. КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Склад Pro Cloud", layout="wide", page_icon="📦")

# Цветовая схема и стили CSS
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; }
    .stDataFrame { border: 1px solid #f0f2f6; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

# Структура таблиц с учетом финансов
SCHEMAS = {
    "batches": ["id", "product_name", "batch_number", "expiry_date", "purchase_price", "min_stock"],
    "transactions": ["id", "batch_id", "type", "quantity", "price", "buyer", "date", "month", "year"],
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
    try:
        # ttl=0 заставляет программу каждый раз запрашивать свежие данные
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet=sheet_name, ttl=0)
        df = df.dropna(how='all')
        
        # Если колонок нет в загруженном DF, принудительно их добавляем
        for col in SCHEMAS[sheet_name]:
            if col not in df.columns:
                df[col] = 0
        return df
    except Exception:
        return pd.DataFrame(columns=SCHEMAS.get(sheet_name, []))
        
        # --- ВОТ ЭТОТ БЛОК ИСПРАВИТ ОШИБКУ ---
        # Проверяем каждую колонку из схемы. Если её нет в таблице — добавляем пустую.
        for col in SCHEMAS[sheet_name]:
            if col not in df.columns:
                # Если это финансовые поля или остатки, заполняем нулями, иначе текстом
                df[col] = 0 if col in ['purchase_price', 'min_stock', 'price'] else ""
        
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
        inventory['calc_qty'] = inventory['calc_qty'].fillna(0).round(2)
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
        # 30 дней и меньше: Желтый фон, Черный текст
        if days <= 30:
            return ['background-color: #fbc02d; color: black; font-weight: bold'] * len(row)
    except: pass
    return [''] * len(row)
def generate_qr(data_text):
    """Создает QR-код, содержащий только ID"""
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    # Очищаем ID от .0, чтобы сканер не путался
    clean_id = str(data_text).split('.')[0]
    qr.add_data(clean_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def read_qr_from_image(image_file):
    """Распознает QR-код с фотографии с камеры"""
    try:
        img = Image.open(image_file)
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img_cv)
        return data
    except:
        return None

def generate_print_sheet(df_selected):
    """Собирает выбранные товары на один лист PNG для печати"""
    qr_w, qr_h = 250, 250
    padding = 60
    cols = 3
    rows = (len(df_selected) + cols - 1) // cols
    sheet_w = (qr_w + padding) * cols + padding
    sheet_h = (qr_h + padding + 120) * rows + padding
    
    canvas = Image.new('RGB', (sheet_w, sheet_h), 'white')
    draw = ImageDraw.Draw(canvas)
    
    for i, (_, row) in enumerate(df_selected.iterrows()):
        clean_id = str(row['id']).split('.')[0]
        qr_bits = generate_qr(clean_id)
        qr_img = Image.open(BytesIO(qr_bits)).resize((qr_w, qr_h))
        
        c, r = i % cols, i // cols
        x = padding + c * (qr_w + padding)
        y = padding + r * (qr_h + padding + 120)
        
        canvas.paste(qr_img, (x, y))
        # Текст под кодом (ID, Название, Партия, Срок)
        info_text = f"ID: {clean_id}\n{row['Товар'][:18]}\nПартия: {row['Партия'][:15]}\nДо: {row['Срок годности']}"
        draw.text((x, y + qr_h + 5), info_text, fill="black")
        
    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
    
# --- 3. ИНТЕРФЕЙС ---

menu = ["📊 Склад", "📥 Приход", "📤 Расход", "📈 Аналитика"]
choice = st.sidebar.radio("Навигация", menu)

# --- РАЗДЕЛ: СКЛАД ---
if choice == "📊 Склад":
    st.header("📦 Текущие остатки")
    df = get_inventory()
    
    if not df.empty:
        st.subheader("Выберите товары для печати QR-кодов:")
        # Создаем копию для редактора с колонкой "Печать"
        df_for_edit = df.copy()
        df_for_edit.insert(0, "Печать", False)
        
        # Интерактивная таблица
        edited_df = st.data_editor(
            df_for_edit[['Печать', 'id', 'Товар', 'Партия', 'Срок годности', 'Остаток']],
            hide_index=True,
            column_config={"Печать": st.column_config.CheckboxColumn("Печать", default=False)},
            use_container_width=True,
            key="inventory_editor"
        )
        
        # Фильтруем то, что пользователь отметил галочкой
        to_print = edited_df[edited_df["Печать"] == True]
        
        if not to_print.empty:
            if st.button(f"📄 Сформировать лист QR для {len(to_print)} поз.", type="primary"):
                sheet = generate_print_sheet(to_print)
                st.image(sheet, caption="Лист для печати")
                st.download_button("📥 Скачать файл для печати", data=sheet, file_name=f"qr_labels_{date.today()}.png")

        st.markdown("---")
        # Ваша кнопка отчета в Telegram
        if st.button("📢 Отправить отчет о сроках в Telegram"):
            # (Тут остается ваш оригинальный код отправки в телеграм)
            today = date.today()
            alerts = []
            for _, r in df.iterrows():
                try:
                    exp = pd.to_datetime(r['Срок годности']).date()
                    if r['Остаток'] > 0 and (exp - today).days <= 30:
                        icon = "🚨" if exp < today else "⚠️"
                        alerts.append(f"{icon} *{r['Товар']}* ({r['Партия']})\nОстаток: {r['Остаток']} | До: {exp}")
                except: continue
            if alerts and bot:
                bot.send_message(TELEGRAM_CHAT_ID, "🛑 *ВНИМАНИЕ: СРОКИ!*\n\n" + "\n\n".join(alerts), parse_mode="Markdown")
                st.success("Отчет отправлен!")
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
        col1, col2, col3 = st.columns(3)
        b_num = col1.text_input("Партия")
        qty = col1.number_input("Количество", min_value=0.1, value=1.0)
        
        # НОВЫЕ ПОЛЯ
        p_price = col2.number_input("Цена закупки (ед.)", min_value=0.0, value=0.0)
        m_stock = col2.number_input("Мин. остаток (алерт)", min_value=0.0, value=4.0)
        exp = col3.date_input("Годен до", min_value=date.today())
        
        if st.form_submit_button("✅ Принять"):
            final_name = p_new if p_sel == "+ Новый товар..." else p_sel
            if final_name and b_num:
                if p_sel == "+ Новый товар...":
                    safe_update("products", pd.concat([df_prod, pd.DataFrame([{"name": final_name}])], ignore_index=True))
                
                df_b = get_data("batches")
                new_id = 1 if df_b.empty else int(pd.to_numeric(df_b['id']).max()) + 1
                # Записываем закупочную цену и мин. остаток в партию
                new_batch = pd.DataFrame([{"id": new_id, "product_name": final_name, "batch_number": b_num, 
                                           "expiry_date": str(exp), "purchase_price": p_price, "min_stock": m_stock}])
                safe_update("batches", pd.concat([df_b, new_batch], ignore_index=True))
                
                df_t = get_data("transactions")
                t_id = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                new_trans = pd.DataFrame([{
                    "id": t_id, "batch_id": new_id, "type": "IN", "quantity": round(float(qty), 2),
                    "price": p_price, "buyer": "СКЛАД", "date": str(date.today()), 
                    "month": date.today().month, "year": date.today().year
                }])
                if safe_update("transactions", pd.concat([df_t, new_trans], ignore_index=True)):
                    st.success("Товар принят и цена закупки сохранена!")
                    time.sleep(1); st.rerun()
                    
# --- РАЗДЕЛ: РАСХОД ---
elif choice == "📤 Расход":
    st.header("💸 Расход (Продажа)")
    
    # Инициализация корзины покупок в памяти (session_state)
    if 'cart' not in st.session_state:
        st.session_state.cart = []

    # --- ШАГ 1: ВЫБОР КЛИЕНТА ---
    st.subheader("1. Покупатель")
    df_clients = get_data("clients")
    c_options = ["+ Новый клиент..."] + (df_clients['name'].tolist() if not df_clients.empty else [])
    
    col_c1, col_c2 = st.columns(2)
    c_sel = col_c1.selectbox("Выберите клиента", c_options)
    # Поле для нового клиента активно только если выбран "+ Новый клиент..."
    c_new = col_c2.text_input("Имя клиента (если новый)", disabled=(c_sel != "+ Новый клиент..."))
    final_client = c_new if c_sel == "+ Новый клиент..." else c_sel

    st.markdown("---")
    
    # --- ШАГ 2: ВЫБОР ТОВАРОВ ---
    st.subheader("2. Добавление товаров")
    df_inv = get_inventory()
    df_avail = df_inv[df_inv['Остаток'] > 0] if not df_inv.empty else pd.DataFrame()
    
    if not df_avail.empty:
        scanned_id = None
        
        # Создаем вкладки: Камера и Ручной поиск
        tab_cam, tab_list = st.tabs(["📷 Сканировать QR", "🔎 Выбрать из списка"])
        
        with tab_cam:
            cam_photo = st.camera_input("Наведите камеру на QR-код")
            if cam_photo:
                qr_val = read_qr_from_image(cam_photo)
                if qr_val:
                    try:
                        scanned_id = int(float(str(qr_val).strip()))
                        st.success(f"✅ Товар распознан! ID: {scanned_id}")
                    except:
                        st.error(f"❌ Неверный формат кода: {qr_val}")
                else:
                    st.warning("QR-код не виден. Попробуйте поднести ближе или улучшить свет.")

        with tab_list:
            items = df_avail.apply(lambda x: f"ID:{x['id']} | {x['Товар']} ({x['Партия']}) | Ост: {x['Остаток']:.2f}", axis=1).tolist()
            manual_choice = st.selectbox("Или найдите товар вручную", ["Не выбрано"] + items)
            if manual_choice != "Не выбрано":
                scanned_id = int(float(manual_choice.split("|")[0].replace("ID:", "")))

        # Если товар выбран (сканером или списком), показываем поле количества
        if scanned_id:
            match = df_avail[df_avail['id'] == scanned_id]
            if not match.empty:
                row = match.iloc[0]
                st.info(f"Выбрано: **{row['Товар']}** (Партия: {row['Партия']})")
                
                # Проверка остатка с учетом корзины
                already_in_cart = sum(item['qty'] for item in st.session_state.cart if item['batch_id'] == scanned_id)
                available_now = float(row['Остаток']) - already_in_cart
                
                col_q1, col_q2 = st.columns([2, 1])
                with col_q1:
                    qty_to_add = st.number_input("Введите количество", min_value=0.01, max_value=max(0.01, available_now), step=1.0, value=min(1.0, available_now))
                # --- ДОБАВИТЬ ЭТО ---
                    sell_price = st.number_input("Цена продажи (за 1 ед.)", min_value=0.0, value=0.0, step=10.0)
                    # --------------------
                    
                    with col_q2:
                        st.write("")
                        st.write("")
                        if st.button("➕ Добавить в корзину", use_container_width=True):
                            st.session_state.cart.append({
                                "batch_id": scanned_id,
                                "item_name": row['Товар'],
                                "qty": round(float(qty_to_add), 2),
                                "price": sell_price  # <-- ДОБАВЬ ЭТУ СТРОЧКУ В СЛОВАРЬ
                            })
                            st.rerun()
            else:
                st.error("Этот товар закончился или ID не существует.")

        # --- ШАГ 3: ОФОРМЛЕНИЕ КОРЗИНЫ ---
        if st.session_state.cart:
            st.markdown("---")
            st.subheader(f"🛒 Заказ для: {final_client if final_client else '⚠️ Клиент не указан'}")
            
            # Собираем корзину в таблицу и суммируем одинаковые позиции
            cart_df = pd.DataFrame(st.session_state.cart)
            display_cart = cart_df.groupby(['batch_id', 'item_name', 'price'])['qty'].sum().reset_index()
            
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
                        st.error("Укажите имя клиента перед оформлением!")
                    else:
                        # 1. Записываем нового клиента в БД, если нужно
                        if c_sel == "+ Новый клиент...":
                            safe_update("clients", pd.concat([df_clients, pd.DataFrame([{"name": final_client}])], ignore_index=True))

                        # 2. Готовим пакет транзакций
                        df_t = get_data("transactions")
                        t_id_start = 1 if df_t.empty else int(pd.to_numeric(df_t['id']).max()) + 1
                        
                        new_transactions = []
                        for i, row in display_cart.iterrows():
                            new_transactions.append({
                                "id": t_id_start + i,
                                "batch_id": row['batch_id'],
                                "type": "OUT",
                                "quantity": round(float(row['qty']), 2),
                                "price": row['price'],  # <-- ДОБАВЬ ЭТУ СТРОКУ
                                "buyer": final_client,
                                "date": str(date.today()),
                                "month": date.today().month,
                                "year": date.today().year
                            })
                            
                        # 3. Отправляем все разом в Google Sheets
                        if safe_update("transactions", pd.concat([df_t, pd.DataFrame(new_transactions)], ignore_index=True)):
                            st.success(f"Заказ оформлен! Списано позиций: {len(new_transactions)}")
                            st.session_state.cart = [] # Очищаем корзину после успеха
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
    st.header("💰 Финансовый отчет и остатки")
    df_t = get_data("transactions")
    df_b = get_data("batches")
    df_inv = get_inventory() # Текущие остатки из вашей функции

    if not df_t.empty and not df_b.empty:
        # --- 1. БЛОК ПРЕДУПРЕЖДЕНИЙ ---
        # --- 1. БЛОК ПРЕДУПРЕЖДЕНИЙ (Безопасная версия) ---
        st.subheader("⚠️ Контроль запасов")
        
        # Проверяем, есть ли колонка min_stock в df_b. Если нет — создаем её в памяти со значением 5
        if 'min_stock' not in df_b.columns:
            df_b['min_stock'] = 4
        
        df_b['min_stock'] = pd.to_numeric(df_b['min_stock'], errors='coerce').fillna(4)
        df_b['id'] = pd.to_numeric(df_b['id'], errors='coerce')
        
        alerts_df = pd.merge(df_inv, df_b[['id', 'min_stock']], on='id', how='left')
        
        # Еще раз проверяем наличие колонки в объединенной таблице
        if 'min_stock' in alerts_df.columns:
            critical = alerts_df[alerts_df['Остаток'] <= alerts_df['min_stock']]
            if not critical.empty:
                st.error(f"Внимание! Закончилось или подходит к концу {len(critical)} товаров:")
                st.table(critical[['Товар', 'Партия', 'Остаток', 'min_stock']])
            else:
                st.success("Всех товаров на складе достаточно.")
        else:
            st.warning("Колонка 'min_stock' не найдена. Проверьте заголовки таблицы.")

        st.divider()

        # --- 2. РАСЧЕТ ДЕНЕГ ---
        # Очищаем данные от ошибок
        df_t['quantity'] = pd.to_numeric(df_t['quantity'], errors='coerce').fillna(0)
        df_t['price'] = pd.to_numeric(df_t['price'], errors='coerce').fillna(0)
        df_b['purchase_price'] = pd.to_numeric(df_b['purchase_price'], errors='coerce').fillna(0)

        # Выручка (только тип OUT)
        sales = df_t[df_t['type'] == 'OUT'].copy()
        revenue = (sales['quantity'] * sales['price']).sum()

        # Расчет себестоимости (подтягиваем закупочную цену из партий)
        cost_data = pd.merge(sales, df_b[['id', 'purchase_price']], left_on='batch_id', right_on='id', how='left')
        total_cost = (cost_data['quantity'] * cost_data['purchase_price']).sum()
        
        profit = revenue - total_cost

        # Красивые карточки
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Общая выручка", f"{revenue:,.0f} ₽")
        col_m2.metric("Себестоимость", f"{total_cost:,.0f} ₽")
        col_m3.metric("Чистая прибыль", f"{profit:,.0f} ₽", delta=f"{int(profit)} ₽")

        # График прибыли по дням
        st.subheader("Динамика прибыли")
        cost_data['daily_profit'] = (cost_data['quantity'] * cost_data['price']) - (cost_data['quantity'] * cost_data['purchase_price'])
        st.line_chart(cost_data.groupby('date')['daily_profit'].sum())

    else:
        st.info("Данных для финансового анализа пока нет. Проведите первую продажу с указанием цены.")






