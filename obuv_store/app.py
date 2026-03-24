"""
ООО "Обувь" - Информационная система магазина обуви
Flask приложение
"""

import os
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, flash, session, g

app = Flask(__name__)
app.secret_key = 'obuv_store_secret_key_2025'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Конфигурация базы данных
DATABASE = os.path.join('instance', 'obuv.db')

# Цвета из руководства по стилю
COLORS = {
    'primary_bg': '#FFFFFF',
    'secondary_bg': '#7FFF00',
    'accent': '#00FA9A',
    'discount_high': '#2E8B57',
    'out_of_stock': '#87CEEB',  # Голубой для отсутствия на складе
}


def get_db():
    """Получение соединения с базой данных"""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Закрытие соединения с базой данных"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    """Выполнение запроса к базе данных"""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    """Выполнение запроса на изменение данных"""
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


# ==================== ДЕКОРАТОРЫ ДОСТУПА ====================

def login_required(f):
    """Декоратор для проверки авторизации"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо авторизоваться', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Декоратор для проверки прав администратора"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо авторизоваться', 'error')
            return redirect(url_for('login'))
        if session.get('role') != 'Администратор':
            flash('У вас нет прав для выполнения этого действия', 'error')
            return redirect(url_for('products'))
        return f(*args, **kwargs)
    return decorated_function


def manager_required(f):
    """Декоратор для проверки прав менеджера или администратора"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо авторизоваться', 'error')
            return redirect(url_for('login'))
        if session.get('role') not in ['Менеджер', 'Администратор']:
            flash('У вас нет прав для выполнения этого действия', 'error')
            return redirect(url_for('products'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== КОНТЕКСТНЫЕ ПРОЦЕССОРЫ ====================

@app.context_processor
def inject_globals():
    """Добавление глобальных переменных в шаблоны"""
    return {
        'colors': COLORS,
        'current_user': session.get('full_name'),
        'user_role': session.get('role')
    }


# ==================== МАРШРУТЫ ====================

@app.route('/')
def index():
    """Главная страница - перенаправление на страницу входа"""
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа"""
    if request.method == 'POST':
        login_name = request.form.get('login')
        password = request.form.get('password')
        
        user = query_db('''
            SELECT u.*, r.name as role_name 
            FROM users u 
            JOIN roles r ON u.role_id = r.id 
            WHERE u.login = ? AND u.password = ?
        ''', (login_name, password), one=True)
        
        if user:
            session['user_id'] = user['id']
            session['full_name'] = user['full_name']
            session['role'] = user['role_name']
            flash(f'Добро пожаловать, {user["full_name"]}!', 'success')
            return redirect(url_for('products'))
        else:
            flash('Неверный логин или пароль', 'error')
    
    return render_template('login.html')


@app.route('/guest')
def guest():
    """Вход как гость"""
    session['user_id'] = None
    session['full_name'] = 'Гость'
    session['role'] = 'Гость'
    flash('Вы вошли как гость. Доступ ограничен.', 'info')
    return redirect(url_for('products'))


@app.route('/logout')
def logout():
    """Выход из системы"""
    session.clear()
    flash('Вы успешно вышли из системы', 'info')
    return redirect(url_for('login'))


# ==================== ТОВАРЫ ====================

@app.route('/products')
def products():
    """Список товаров"""
    user_role = session.get('role', 'Гость')
    
    # Базовый запрос
    query = '''
        SELECT p.*, s.name as supplier_name, m.name as manufacturer_name, c.name as category_name
        FROM products p
        JOIN suppliers s ON p.supplier_id = s.id
        JOIN manufacturers m ON p.manufacturer_id = m.id
        JOIN categories c ON p.category_id = c.id
    '''
    
    params = []
    where_clauses = []
    
    # Поиск (только для менеджера и администратора)
    search = request.args.get('search', '')
    if search and user_role in ['Менеджер', 'Администратор']:
        where_clauses.append('''
            (p.name LIKE ? OR p.article LIKE ? OR p.description LIKE ? 
             OR s.name LIKE ? OR m.name LIKE ? OR c.name LIKE ?)
        ''')
        search_param = f'%{search}%'
        params.extend([search_param] * 6)
    
    # Фильтр по поставщику (только для менеджера и администратора)
    supplier_filter = request.args.get('supplier', '')
    if supplier_filter and supplier_filter != 'all' and user_role in ['Менеджер', 'Администратор']:
        where_clauses.append('p.supplier_id = ?')
        params.append(supplier_filter)
    
    if where_clauses:
        query += ' WHERE ' + ' AND '.join(where_clauses)
    
    # Сортировка (только для менеджера и администратора)
    sort = request.args.get('sort', '')
    if sort and user_role in ['Менеджер', 'Администратор']:
        if sort == 'quantity_asc':
            query += ' ORDER BY p.quantity ASC'
        elif sort == 'quantity_desc':
            query += ' ORDER BY p.quantity DESC'
    
    products_list = query_db(query, params)
    
    # Получаем список поставщиков для фильтра
    suppliers = query_db('SELECT * FROM suppliers ORDER BY name')
    
    return render_template('products.html', 
                         products=products_list, 
                         suppliers=suppliers,
                         search=search,
                         supplier_filter=supplier_filter,
                         sort=sort)


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    """Детальная информация о товаре"""
    product = query_db('''
        SELECT p.*, s.name as supplier_name, m.name as manufacturer_name, c.name as category_name
        FROM products p
        JOIN suppliers s ON p.supplier_id = s.id
        JOIN manufacturers m ON p.manufacturer_id = m.id
        JOIN categories c ON p.category_id = c.id
        WHERE p.id = ?
    ''', (product_id,), one=True)
    
    if not product:
        flash('Товар не найден', 'error')
        return redirect(url_for('products'))
    
    return render_template('product_detail.html', product=product)


# ==================== ДОБАВЛЕНИЕ/РЕДАКТИРОВАНИЕ ТОВАРА ====================

@app.route('/product/add', methods=['GET', 'POST'])
@admin_required
def add_product():
    """Добавление нового товара"""
    if request.method == 'POST':
        # Валидация данных
        article = request.form.get('article', '').strip()
        name = request.form.get('name', '').strip()
        unit = request.form.get('unit', '').strip()
        price = request.form.get('price', '')
        supplier_id = request.form.get('supplier_id', '')
        manufacturer_id = request.form.get('manufacturer_id', '')
        category_id = request.form.get('category_id', '')
        discount = request.form.get('discount', '0')
        quantity = request.form.get('quantity', '0')
        description = request.form.get('description', '').strip()
        
        # Проверка обязательных полей
        errors = []
        if not article:
            errors.append('Артикул обязателен для заполнения')
        if not name:
            errors.append('Наименование товара обязательно для заполнения')
        if not price:
            errors.append('Цена обязательна для заполнения')
        try:
            price_val = float(price)
            if price_val < 0:
                errors.append('Цена не может быть отрицательной')
        except:
            errors.append('Цена должна быть числом')
        
        try:
            discount_val = int(discount) if discount else 0
            if discount_val < 0:
                errors.append('Скидка не может быть отрицательной')
        except:
            errors.append('Скидка должна быть целым числом')
        
        try:
            quantity_val = int(quantity) if quantity else 0
            if quantity_val < 0:
                errors.append('Количество не может быть отрицательным')
        except:
            errors.append('Количество должно быть целым числом')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            # Получаем справочники для формы
            suppliers = query_db('SELECT * FROM suppliers ORDER BY name')
            manufacturers = query_db('SELECT * FROM manufacturers ORDER BY name')
            categories = query_db('SELECT * FROM categories ORDER BY name')
            return render_template('product_form.html', 
                                 product=None,
                                 suppliers=suppliers,
                                 manufacturers=manufacturers,
                                 categories=categories,
                                 form_data=request.form)
        
        # Обработка загрузки фото
        photo_filename = 'picture.png'
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                photo_filename = process_uploaded_image(file)
        
        # Добавление товара в базу
        try:
            product_id = execute_db('''
                INSERT INTO products (article, name, unit, price, supplier_id, manufacturer_id, 
                                    category_id, discount, quantity, description, photo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (article, name, unit, float(price), int(supplier_id), int(manufacturer_id),
                  int(category_id), discount_val, quantity_val, description, photo_filename))
            
            flash('Товар успешно добавлен', 'success')
            return redirect(url_for('products'))
        except sqlite3.IntegrityError:
            flash('Товар с таким артикулом уже существует', 'error')
    
    # GET запрос - показываем форму
    suppliers = query_db('SELECT * FROM suppliers ORDER BY name')
    manufacturers = query_db('SELECT * FROM manufacturers ORDER BY name')
    categories = query_db('SELECT * FROM categories ORDER BY name')
    
    return render_template('product_form.html',
                         product=None,
                         suppliers=suppliers,
                         manufacturers=manufacturers,
                         categories=categories)


@app.route('/product/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    """Редактирование товара"""
    product = query_db('SELECT * FROM products WHERE id = ?', (product_id,), one=True)
    
    if not product:
        flash('Товар не найден', 'error')
        return redirect(url_for('products'))
    
    if request.method == 'POST':
        # Валидация данных
        article = request.form.get('article', '').strip()
        name = request.form.get('name', '').strip()
        unit = request.form.get('unit', '').strip()
        price = request.form.get('price', '')
        supplier_id = request.form.get('supplier_id', '')
        manufacturer_id = request.form.get('manufacturer_id', '')
        category_id = request.form.get('category_id', '')
        discount = request.form.get('discount', '0')
        quantity = request.form.get('quantity', '0')
        description = request.form.get('description', '').strip()
        
        # Проверка обязательных полей
        errors = []
        if not name:
            errors.append('Наименование товара обязательно для заполнения')
        if not price:
            errors.append('Цена обязательна для заполнения')
        try:
            price_val = float(price)
            if price_val < 0:
                errors.append('Цена не может быть отрицательной')
        except:
            errors.append('Цена должна быть числом')
        
        try:
            discount_val = int(discount) if discount else 0
            if discount_val < 0:
                errors.append('Скидка не может быть отрицательной')
        except:
            errors.append('Скидка должна быть целым числом')
        
        try:
            quantity_val = int(quantity) if quantity else 0
            if quantity_val < 0:
                errors.append('Количество не может быть отрицательным')
        except:
            errors.append('Количество должно быть целым числом')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            suppliers = query_db('SELECT * FROM suppliers ORDER BY name')
            manufacturers = query_db('SELECT * FROM manufacturers ORDER BY name')
            categories = query_db('SELECT * FROM categories ORDER BY name')
            return render_template('product_form.html',
                                 product=product,
                                 suppliers=suppliers,
                                 manufacturers=manufacturers,
                                 categories=categories)
        
        # Обработка загрузки фото
        photo_filename = product['photo']
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                # Удаляем старое фото если оно не picture.png
                if photo_filename and photo_filename != 'picture.png':
                    old_photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                photo_filename = process_uploaded_image(file)
        
        # Обновление товара
        execute_db('''
            UPDATE products 
            SET article = ?, name = ?, unit = ?, price = ?, supplier_id = ?, 
                manufacturer_id = ?, category_id = ?, discount = ?, quantity = ?, 
                description = ?, photo = ?
            WHERE id = ?
        ''', (article, name, unit, float(price), int(supplier_id), int(manufacturer_id),
              int(category_id), discount_val, quantity_val, description, photo_filename, product_id))
        
        flash('Товар успешно обновлен', 'success')
        return redirect(url_for('products'))
    
    # GET запрос - показываем форму
    suppliers = query_db('SELECT * FROM suppliers ORDER BY name')
    manufacturers = query_db('SELECT * FROM manufacturers ORDER BY name')
    categories = query_db('SELECT * FROM categories ORDER BY name')
    
    return render_template('product_form.html',
                         product=product,
                         suppliers=suppliers,
                         manufacturers=manufacturers,
                         categories=categories)


@app.route('/product/delete/<int:product_id>', methods=['POST'])
@admin_required
def delete_product(product_id):
    """Удаление товара"""
    # Проверяем, есть ли товар в заказах
    order_items = query_db('SELECT * FROM order_items WHERE product_id = ?', (product_id,))
    if order_items:
        flash('Нельзя удалить товар, который присутствует в заказах', 'error')
        return redirect(url_for('products'))
    
    # Получаем информацию о товаре для удаления фото
    product = query_db('SELECT photo FROM products WHERE id = ?', (product_id,), one=True)
    if product and product['photo'] and product['photo'] != 'picture.png':
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], product['photo'])
        if os.path.exists(photo_path):
            os.remove(photo_path)
    
    execute_db('DELETE FROM products WHERE id = ?', (product_id,))
    flash('Товар успешно удален', 'success')
    return redirect(url_for('products'))


def process_uploaded_image(file):
    """Обработка загруженного изображения (ресайз до 300x200)"""
    filename = secure_filename(file.filename)
    # Добавляем timestamp к имени файла для уникальности
    name, ext = os.path.splitext(filename)
    filename = f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Ресайз изображения до 300x200
    with Image.open(filepath) as img:
        img = img.convert('RGB')
        img = img.resize((300, 200), Image.Resampling.LANCZOS)
        img.save(filepath, 'JPEG', quality=85)
    
    return filename


# ==================== ЗАКАЗЫ ====================

@app.route('/orders')
@manager_required
def orders():
    """Список заказов"""
    orders_list = query_db('''
        SELECT o.*, u.full_name as user_name, s.name as status_name, pp.address as pickup_address
        FROM orders o
        JOIN users u ON o.user_id = u.id
        JOIN order_statuses s ON o.status_id = s.id
        JOIN pickup_points pp ON o.pickup_point_id = pp.id
        ORDER BY o.order_date DESC
    ''')
    
    return render_template('orders.html', orders=orders_list)


@app.route('/order/<int:order_id>')
@manager_required
def order_detail(order_id):
    """Детальная информация о заказе"""
    order = query_db('''
        SELECT o.*, u.full_name as user_name, s.name as status_name, pp.address as pickup_address
        FROM orders o
        JOIN users u ON o.user_id = u.id
        JOIN order_statuses s ON o.status_id = s.id
        JOIN pickup_points pp ON o.pickup_point_id = pp.id
        WHERE o.id = ?
    ''', (order_id,), one=True)
    
    if not order:
        flash('Заказ не найден', 'error')
        return redirect(url_for('orders'))
    
    # Получаем позиции заказа
    items = query_db('''
        SELECT oi.*, p.name as product_name, p.article, p.price, p.discount
        FROM order_items oi
        JOIN products p ON oi.product_id = p.id
        WHERE oi.order_id = ?
    ''', (order_id,))
    
    return render_template('order_detail.html', order=order, items=items)


@app.route('/order/add', methods=['GET', 'POST'])
@admin_required
def add_order():
    """Добавление нового заказа"""
    if request.method == 'POST':
        order_number = request.form.get('order_number', '').strip()
        user_id = request.form.get('user_id', '')
        pickup_point_id = request.form.get('pickup_point_id', '')
        order_date = request.form.get('order_date', '')
        delivery_date = request.form.get('delivery_date', '')
        code = request.form.get('code', '').strip()
        status_id = request.form.get('status_id', '')
        
        # Валидация
        errors = []
        if not order_number:
            errors.append('Номер заказа обязателен')
        if not user_id:
            errors.append('Клиент обязателен')
        if not pickup_point_id:
            errors.append('Пункт выдачи обязателен')
        if not order_date:
            errors.append('Дата заказа обязательна')
        if not code:
            errors.append('Код для получения обязателен')
        if not status_id:
            errors.append('Статус обязателен')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            users = query_db('SELECT * FROM users WHERE role_id = 2 ORDER BY full_name')
            pickup_points = query_db('SELECT * FROM pickup_points ORDER BY address')
            statuses = query_db('SELECT * FROM order_statuses ORDER BY name')
            return render_template('order_form.html',
                                 order=None,
                                 users=users,
                                 pickup_points=pickup_points,
                                 statuses=statuses,
                                 form_data=request.form)
        
        try:
            order_id = execute_db('''
                INSERT INTO orders (order_number, user_id, pickup_point_id, order_date, 
                                  delivery_date, code, status_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (int(order_number), int(user_id), int(pickup_point_id), order_date,
                  delivery_date if delivery_date else None, code, int(status_id)))
            
            flash('Заказ успешно добавлен', 'success')
            return redirect(url_for('orders'))
        except sqlite3.IntegrityError:
            flash('Заказ с таким номером уже существует', 'error')
    
    # GET запрос
    users = query_db('SELECT * FROM users WHERE role_id = 2 ORDER BY full_name')
    pickup_points = query_db('SELECT * FROM pickup_points ORDER BY address')
    statuses = query_db('SELECT * FROM order_statuses ORDER BY name')
    
    return render_template('order_form.html',
                         order=None,
                         users=users,
                         pickup_points=pickup_points,
                         statuses=statuses)


@app.route('/order/edit/<int:order_id>', methods=['GET', 'POST'])
@admin_required
def edit_order(order_id):
    """Редактирование заказа"""
    order = query_db('SELECT * FROM orders WHERE id = ?', (order_id,), one=True)
    
    if not order:
        flash('Заказ не найден', 'error')
        return redirect(url_for('orders'))
    
    if request.method == 'POST':
        order_number = request.form.get('order_number', '').strip()
        user_id = request.form.get('user_id', '')
        pickup_point_id = request.form.get('pickup_point_id', '')
        order_date = request.form.get('order_date', '')
        delivery_date = request.form.get('delivery_date', '')
        code = request.form.get('code', '').strip()
        status_id = request.form.get('status_id', '')
        
        # Валидация
        errors = []
        if not order_number:
            errors.append('Номер заказа обязателен')
        if not user_id:
            errors.append('Клиент обязателен')
        if not pickup_point_id:
            errors.append('Пункт выдачи обязателен')
        if not order_date:
            errors.append('Дата заказа обязательна')
        if not code:
            errors.append('Код для получения обязателен')
        if not status_id:
            errors.append('Статус обязателен')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            users = query_db('SELECT * FROM users WHERE role_id = 2 ORDER BY full_name')
            pickup_points = query_db('SELECT * FROM pickup_points ORDER BY address')
            statuses = query_db('SELECT * FROM order_statuses ORDER BY name')
            return render_template('order_form.html',
                                 order=order,
                                 users=users,
                                 pickup_points=pickup_points,
                                 statuses=statuses)
        
        execute_db('''
            UPDATE orders 
            SET order_number = ?, user_id = ?, pickup_point_id = ?, order_date = ?,
                delivery_date = ?, code = ?, status_id = ?
            WHERE id = ?
        ''', (int(order_number), int(user_id), int(pickup_point_id), order_date,
              delivery_date if delivery_date else None, code, int(status_id), order_id))
        
        flash('Заказ успешно обновлен', 'success')
        return redirect(url_for('orders'))
    
    # GET запрос
    users = query_db('SELECT * FROM users WHERE role_id = 2 ORDER BY full_name')
    pickup_points = query_db('SELECT * FROM pickup_points ORDER BY address')
    statuses = query_db('SELECT * FROM order_statuses ORDER BY name')
    
    return render_template('order_form.html',
                         order=order,
                         users=users,
                         pickup_points=pickup_points,
                         statuses=statuses)


@app.route('/order/delete/<int:order_id>', methods=['POST'])
@admin_required
def delete_order(order_id):
    """Удаление заказа"""
    # Сначала удаляем позиции заказа
    execute_db('DELETE FROM order_items WHERE order_id = ?', (order_id,))
    # Затем удаляем сам заказ
    execute_db('DELETE FROM orders WHERE id = ?', (order_id,))
    flash('Заказ успешно удален', 'success')
    return redirect(url_for('orders'))


# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================

if __name__ == '__main__':
    # Создаем папку для загрузок если её нет
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=5000)
