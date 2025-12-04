"""
Telegram-бот для получения погоды через OpenWeather API
"""
import os
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import telebot
from telebot import types

from weather_app import WeatherAPI
from storage import UserStorage
from utils import (
    format_forecast_day, format_datetime_ru, validate_city_name,
    validate_coordinates, validate_notification_interval,
    translate_country_code, convert_pressure_hpa_to_mmhg
)

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токены из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
OW_API_KEY = os.getenv('OW_API_KEY')

if not BOT_TOKEN or BOT_TOKEN == 'your_telegram_token':
    raise ValueError("BOT_TOKEN не установлен в .env файле")

if not OW_API_KEY or OW_API_KEY == 'your_openweather_key':
    raise ValueError("OW_API_KEY не установлен в .env файле")


class WeatherBot:
    """Класс Telegram-бота для погоды"""
    
    def __init__(self):
        """Инициализация бота"""
        self.bot = telebot.TeleBot(BOT_TOKEN)
        self.weather_api = WeatherAPI(OW_API_KEY)
        self.storage = UserStorage()
        
        # Состояния пользователей для многошаговых диалогов
        self.user_states = {}
        
        # Регистрируем обработчики
        self._register_handlers()
        
        # Устанавливаем команды бота (убираем /clear)
        self._set_bot_commands()
        
        # Запускаем поток для уведомлений
        self._start_notification_thread()
        
        logger.info("Бот инициализирован и готов к работе")
    
    def _set_bot_commands(self):
        """Устанавливает команды бота"""
        from telebot import types as bot_types
        commands = [
            bot_types.BotCommand("start", "Главное меню"),
            bot_types.BotCommand("help", "Помощь по использованию"),
        ]
        try:
            self.bot.set_my_commands(commands)
            logger.info("Команды бота установлены")
        except Exception as e:
            logger.warning(f"Не удалось установить команды бота: {e}")
    
    def _register_handlers(self):
        """Регистрирует все обработчики команд и сообщений"""
        
        @self.bot.message_handler(commands=['start'])
        def start_command(message):
            self.handle_start(message)
        
        @self.bot.message_handler(commands=['help'])
        def help_command(message):
            self.handle_help(message)
        
        @self.bot.message_handler(content_types=['text'])
        def text_message(message):
            self.handle_text(message)
        
        @self.bot.message_handler(content_types=['location'])
        def location_message(message):
            self.handle_location(message)
        
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_handler(call):
            self.handle_callback(call)
        
        @self.bot.inline_handler(func=lambda query: True)
        def inline_query_handler(query):
            self.handle_inline_query(query)
    
    def _start_notification_thread(self):
        """Запускает поток для проверки и отправки уведомлений"""
        def notification_worker():
            while True:
                try:
                    time.sleep(300)  # Проверка каждые 5 минут
                    self._check_and_send_notifications()
                except Exception as e:
                    logger.error(f"Ошибка в потоке уведомлений: {e}")
        
        thread = threading.Thread(target=notification_worker, daemon=True)
        thread.start()
        logger.info("Поток уведомлений запущен")
    
    def _check_and_send_notifications(self):
        """Проверяет и отправляет уведомления пользователям"""
        all_users = self.storage.load_all()
        
        for user_id_str, user_data in all_users.items():
            try:
                user_id = int(user_id_str)
                notifications = user_data.get('notifications', {})
                
                if not notifications.get('enabled', False):
                    continue
                
                # Проверяем, нужно ли отправить уведомление
                last_sent_str = notifications.get('last_sent')
                interval_h = notifications.get('interval_h', 2)
                
                if last_sent_str:
                    try:
                        last_sent = datetime.strptime(last_sent_str, "%Y-%m-%d %H:%M:%S")
                        time_passed = datetime.now() - last_sent
                        if time_passed < timedelta(hours=interval_h):
                            continue
                    except:
                        pass
                
                # Отправляем уведомление
                self._send_notification(user_id, user_data)
            
            except Exception as e:
                logger.error(f"Ошибка при проверке уведомлений для пользователя {user_id_str}: {e}")
    
    def _send_notification(self, user_id: int, user_data: Dict[str, Any]):
        """Отправляет уведомление о погоде пользователю"""
        try:
            lat = user_data.get('lat')
            lon = user_data.get('lon')
            city = user_data.get('city', 'Неизвестно')
            
            if lat is None or lon is None:
                return
            
            weather = self.weather_api.get_current_weather(lat, lon)
            if not weather:
                return
            
            temp = weather.get('main', {}).get('temp', 'N/A')
            desc = weather.get('weather', [{}])[0].get('description_ru', 'N/A')
            humidity = weather.get('main', {}).get('humidity', 'N/A')
            wind = weather.get('wind', {}).get('speed', 'N/A')
            
            message = (
                f"🌡️ Уведомление о погоде\n\n"
                f"📍 {city}\n"
                f"Температура: {temp}°C\n"
                f"Описание: {desc}\n"
                f"Влажность: {humidity}%\n"
                f"Ветер: {wind} м/с"
            )
            
            self.bot.send_message(user_id, message)
            
            # Обновляем время последней отправки
            self.storage.update_user_notification(
                user_id,
                True,
                user_data.get('notifications', {}).get('interval_h', 2),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            
            logger.info(f"Отправлено уведомление пользователю {user_id}")
        
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
    
    def _get_main_menu_keyboard(self):
        """Создает клавиатуру главного меню"""
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        keyboard.add(
            types.KeyboardButton("🌡️ Текущая погода"),
            types.KeyboardButton("📅 Прогноз на 5 дней"),
            types.KeyboardButton("📍 Моя геолокация"),
            types.KeyboardButton("🌍 Сравнить города"),
            types.KeyboardButton("💨 Расширенные данные"),
            types.KeyboardButton("🔔 Уведомления"),
            types.KeyboardButton("📖 Помощь")
        )
        return keyboard
    
    def _get_back_keyboard(self):
        """Создает клавиатуру с кнопкой 'Назад в меню'"""
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        keyboard.add(types.KeyboardButton("⬅️ Назад в меню"))
        return keyboard
    
    def handle_start(self, message):
        """Обработчик команды /start"""
        user_id = message.from_user.id
        
        # Загружаем или создаем данные пользователя
        user_data = self.storage.load_user(user_id)
        if not user_data:
            self.storage.save_user(user_id, {
                'notifications': {
                    'enabled': False,
                    'interval_h': 2
                }
            })
        
        # Очищаем состояние пользователя
        self.user_states.pop(user_id, None)
        
        keyboard = self._get_main_menu_keyboard()
        
        welcome_text = (
            "🌤️ <b>Добро пожаловать в бот погоды!</b>\n\n"
            "📋 <b>Доступные функции:</b>\n\n"
            "• 🌡️ Текущая погода - актуальная погода по городу\n\n"
            "• 📅 Прогноз на 5 дней - детальный прогноз с выбором дня\n\n"
            "• 📍 Моя геолокация - сохраните ваше местоположение\n\n"
            "• 🌍 Сравнить города - сравнение погоды в двух городах\n\n"
            "• 💨 Расширенные данные - погода + качество воздуха\n\n"
            "• 🔔 Уведомления - автоматические уведомления о погоде\n\n"
            "💡 <i>Выберите действие из меню или просто введите название города</i>"
        )
        
        self.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard, parse_mode='HTML')
        logger.info(f"Пользователь {user_id} запустил бота")
    
    def handle_help(self, message):
        """Обработчик команды /help"""
        keyboard = self._get_main_menu_keyboard()
        help_text = (
            "📖 <b>Помощь по использованию бота</b>\n\n"
            "🌡️ <b>Текущая погода</b>\n"
            "Получите актуальную погоду по названию города или используйте сохраненную геолокацию.\n\n"
            "📅 <b>Прогноз на 5 дней</b>\n"
            "Детальный прогноз погоды с выбором дня и просмотром по часам.\n\n"
            "📍 <b>Моя геолокация</b>\n"
            "Сохраните ваше местоположение для быстрого доступа к погоде.\n\n"
            "🌍 <b>Сравнить города</b>\n"
            "Сравните температуру, влажность и другие параметры в двух городах.\n\n"
            "💨 <b>Расширенные данные</b>\n"
            "Полная информация о погоде + анализ качества воздуха.\n\n"
            "🔔 <b>Уведомления</b>\n"
            "Настройте автоматические уведомления о погоде с выбранным интервалом.\n\n"
            "💡 <i>Совет: Вы можете просто ввести название города в любом месте для быстрого получения погоды!</i>"
        )
        self.bot.send_message(message.chat.id, help_text, reply_markup=keyboard, parse_mode='HTML')
    
    def handle_text(self, message):
        """Обработчик текстовых сообщений"""
        text = message.text.strip()
        user_id = message.from_user.id
        
        # Проверяем состояние пользователя
        state = self.user_states.get(user_id, {})
        
        # Обработка кнопок навигации
        if text == "⬅️ Назад в меню" or text == "⬅️ Назад":
            self.user_states.pop(user_id, None)
            keyboard = self._get_main_menu_keyboard()
            self.bot.send_message(
                message.chat.id,
                "🏠 <b>Главное меню</b>\n\nВыберите действие:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            return
        
        if text == "🌡️ Текущая погода":
            self.handle_current_weather_request(message)
        
        elif text == "📅 Прогноз на 5 дней":
            self.handle_forecast_request(message)
        
        elif text == "📍 Моя геолокация":
            self.handle_location_request(message)
        
        elif text == "🌍 Сравнить города":
            self.handle_compare_request(message)
        
        elif text == "💨 Расширенные данные":
            self.handle_extended_data_request(message)
        
        elif text == "🔔 Уведомления":
            self.handle_notifications_menu(message)
        
        elif text == "📖 Помощь":
            self.handle_help(message)
        
        elif text == "🏙️ Ввести город":
            self.user_states[user_id] = {'waiting_for_city': True}
            keyboard = self._get_back_keyboard()
            self.bot.send_message(
                message.chat.id,
                "🏙️ <b>Введите название города:</b>\n\n<i>Например: Москва, London, New York</i>",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        
        elif state.get('waiting_for_city'):
            # Пользователь вводит город для текущей погоды
            self.handle_city_input(message, 'current')
        
        elif state.get('waiting_for_city1'):
            # Пользователь вводит первый город для сравнения
            self.handle_city_input(message, 'compare1')
        
        elif state.get('waiting_for_city2'):
            # Пользователь вводит второй город для сравнения
            self.handle_city_input(message, 'compare2')
        
        else:
            # Пытаемся обработать как название города
            if validate_city_name(text):
                self.handle_city_weather(message, text)
            else:
                keyboard = self._get_main_menu_keyboard()
                self.bot.send_message(
                    message.chat.id,
                    "❓ <b>Не понял команду</b>\n\n"
                    "Пожалуйста, выберите действие из меню или введите название города.\n\n"
                    "💡 <i>Используйте кнопку '📖 Помощь' для получения информации</i>",
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
    
    def handle_current_weather_request(self, message):
        """Обработчик запроса текущей погоды"""
        user_id = message.from_user.id
        user_data = self.storage.load_user(user_id)
        
        # Проверяем, есть ли сохраненная геолокация
        lat = user_data.get('lat')
        lon = user_data.get('lon')
        city = user_data.get('city', '')
        
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        
        if lat and lon and city:
            # Показываем быстрый доступ к сохраненной геолокации
            keyboard.add(
                types.KeyboardButton(f"📍 {city} (моя геолокация)", request_location=True),
                types.KeyboardButton("🏙️ Ввести другой город"),
                types.KeyboardButton("⬅️ Назад в меню")
            )
            text = (
                f"🌡️ <b>Текущая погода</b>\n\n"
                f"📍 <b>Сохраненная геолокация:</b> {city}\n\n"
                f"Выберите способ получения погоды:"
            )
        else:
            keyboard.add(
                types.KeyboardButton("📍 Отправить геолокацию", request_location=True),
                types.KeyboardButton("🏙️ Ввести город"),
                types.KeyboardButton("⬅️ Назад в меню")
            )
            text = (
                "🌡️ <b>Текущая погода</b>\n\n"
                "Выберите способ получения погоды:\n"
                "• Отправьте геолокацию\n"
                "• Или введите название города"
            )
        
        self.bot.send_message(message.chat.id, text, reply_markup=keyboard, parse_mode='HTML')
    
    def handle_city_input(self, message, mode: str):
        """Обработчик ввода города"""
        city = message.text.strip()
        user_id = message.from_user.id
        
        if not validate_city_name(city):
            self.bot.send_message(message.chat.id, "Пожалуйста, введите корректное название города (минимум 2 символа).")
            return
        
        if mode == 'current':
            self.user_states.pop(user_id, None)
            self.handle_city_weather(message, city)
        
        elif mode == 'compare1':
            self.user_states[user_id] = {
                'city1': city,
                'waiting_for_city2': True
            }
            keyboard = self._get_back_keyboard()
            self.bot.send_message(
                message.chat.id,
                f"✅ <b>Город 1:</b> {city}\n\n"
                f"Теперь введите название <b>второго города</b>:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        
        elif mode == 'compare2':
            city1 = self.user_states.get(user_id, {}).get('city1')
            if city1:
                self.user_states.pop(user_id, None)
                self.handle_compare_cities(message, city1, city)
    
    def handle_city_weather(self, message, city: str):
        """Получает и отправляет погоду по городу"""
        msg = self.bot.send_message(message.chat.id, f"🔍 Ищу погоду для <b>{city}</b>...", parse_mode='HTML')
        
        coords = self.weather_api.get_coordinates(city)
        if not coords:
            self.bot.send_message(message.chat.id, f"❌ Город '{city}' не найден. Проверьте название и попробуйте снова.")
            return
        
        lat, lon = coords
        weather = self.weather_api.get_current_weather(lat, lon)
        
        if not weather:
            self.bot.send_message(message.chat.id, "❌ Не удалось получить данные о погоде. Попробуйте позже.")
            return
        
        # Форматируем ответ
        city_name = weather.get('name', city)
        country = weather.get('sys', {}).get('country', '')
        temp = weather.get('main', {}).get('temp', 'N/A')
        feels_like = weather.get('main', {}).get('feels_like', 'N/A')
        humidity = weather.get('main', {}).get('humidity', 'N/A')
        pressure = weather.get('main', {}).get('pressure', 'N/A')
        wind_speed = weather.get('wind', {}).get('speed', 'N/A')
        wind_deg = weather.get('wind', {}).get('deg', 'N/A')
        desc = weather.get('weather', [{}])[0].get('description_ru', 'N/A')
        
        # Направление ветра
        wind_directions = {
            (0, 22.5): "С", (22.5, 67.5): "СВ", (67.5, 112.5): "В",
            (112.5, 157.5): "ЮВ", (157.5, 202.5): "Ю", (202.5, 247.5): "ЮЗ",
            (247.5, 292.5): "З", (292.5, 337.5): "СЗ", (337.5, 360): "С"
        }
        wind_dir = "?"
        if wind_deg != 'N/A':
            for (start, end), direction in wind_directions.items():
                if start <= wind_deg < end or (start == 0 and wind_deg == 0):
                    wind_dir = direction
                    break
        
        # Переводим код страны на русский
        country_ru = translate_country_code(country)
        
        # Конвертируем давление в мм.рт.ст.
        pressure_mmhg = convert_pressure_hpa_to_mmhg(pressure)
        pressure_text = f"{pressure_mmhg} мм.рт.ст." if pressure_mmhg != 'N/A' else "N/A"
        
        # Сохраняем последний запрошенный город/координаты для использования в расширенных данных и прогнозе
        user_id = message.from_user.id
        self.storage.save_user(user_id, {
            'last_city': city_name,
            'last_lat': lat,
            'last_lon': lon,
            'last_country': country_ru
        })
        
        message_text = (
            f"🌡️ <b>{city_name}, {country_ru}</b>\n\n"
            f"<b>Температура:</b> {temp}°C\n\n"
            f"🤔 <b>Ощущается как:</b> {feels_like}°C\n\n"
            f"💧 <b>Влажность:</b> {humidity}%\n\n"
            f"📊 <b>Давление:</b> {pressure_text}\n\n"
            f"💨 <b>Ветер:</b> {wind_speed} м/с {wind_dir}\n\n"
            f"☁️ <b>Описание:</b> {desc.capitalize()}"
        )
        
        keyboard = self._get_back_keyboard()
        self.bot.send_message(message.chat.id, message_text, reply_markup=keyboard, parse_mode='HTML')
        logger.info(f"Отправлена погода для города {city} пользователю {message.from_user.id}")
    
    def handle_location(self, message):
        """Обработчик геолокации"""
        user_id = message.from_user.id
        location = message.location
        
        if not location:
            self.bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте местоположение.")
            return
        
        lat = location.latitude
        lon = location.longitude
        
        if not validate_coordinates(lat, lon):
            self.bot.send_message(message.chat.id, "❌ Некорректные координаты.")
            return
        
        # Получаем название города по координатам (обратный геокодинг)
        msg = self.bot.send_message(message.chat.id, "🔍 Определяю местоположение...")
        
        # Используем текущую погоду для получения названия города
        weather = self.weather_api.get_current_weather(lat, lon)
        city_name = "Неизвестно"
        
        if weather:
            city_name = weather.get('name', 'Неизвестно')
        
        # Сохраняем геолокацию
        self.storage.update_user_location(user_id, city_name, lat, lon)
        
        # Сохраняем последний запрошенный город/координаты для использования в расширенных данных и прогнозе
        country = weather.get('sys', {}).get('country', '') if weather else ''
        country_ru = translate_country_code(country) if country else ''
        self.storage.save_user(user_id, {
            'last_city': city_name,
            'last_lat': lat,
            'last_lon': lon,
            'last_country': country_ru
        })
        
        # Отправляем погоду
        if weather:
            self.handle_city_weather(message, city_name)
        else:
            keyboard = self._get_back_keyboard()
            self.bot.send_message(
                message.chat.id,
                f"✅ <b>Геолокация сохранена</b>\n\n"
                f"Координаты: {lat}, {lon}\n"
                f"Не удалось получить название города.",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        
        logger.info(f"Сохранена геолокация для пользователя {user_id}: {city_name}")
    
    def handle_location_request(self, message):
        """Запрос геолокации от пользователя"""
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(types.KeyboardButton("📍 Отправить геолокацию", request_location=True))
        keyboard.add(types.KeyboardButton("⬅️ Назад в меню"))
        
        user_data = self.storage.load_user(message.from_user.id)
        city = user_data.get('city', '')
        
        if city:
            text = (
                f"📍 <b>Моя геолокация</b>\n\n"
                f"Текущая сохраненная геолокация: <b>{city}</b>\n\n"
                f"Отправьте новую геолокацию для обновления:"
            )
        else:
            text = (
                "📍 <b>Моя геолокация</b>\n\n"
                "Отправьте вашу геолокацию для сохранения.\n"
                "После сохранения вы сможете быстро получать погоду для вашего местоположения."
            )
        
        self.bot.reply_to(message, text, reply_markup=keyboard, parse_mode='HTML')
    
    def handle_forecast_request(self, message):
        """Обработчик запроса прогноза на 5 дней"""
        user_id = message.from_user.id
        user_data = self.storage.load_user(user_id)
        
        # Используем последний запрошенный город/координаты, если есть
        lat = user_data.get('last_lat') or user_data.get('lat')
        lon = user_data.get('last_lon') or user_data.get('lon')
        city = user_data.get('last_city') or user_data.get('city', '')
        
        if not lat or not lon:
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
            keyboard.add(
                types.KeyboardButton("📍 Отправить геолокацию", request_location=True),
                types.KeyboardButton("🏙️ Ввести город"),
                types.KeyboardButton("⬅️ Назад в меню")
            )
            self.bot.send_message(
                message.chat.id,
                "📅 <b>Прогноз на 5 дней</b>\n\n"
                "Для получения прогноза необходимо:\n"
                "• Сохранить геолокацию, или\n"
                "• Ввести название города\n\n"
                "Выберите способ:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            self.user_states[user_id] = {'waiting_for_city': True, 'forecast_mode': True}
            return
        
        keyboard = self._get_back_keyboard()
        self.bot.send_message(
            message.chat.id,
            f"🔍 Получаю прогноз для <b>{city}</b>...",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        forecast = self.weather_api.get_forecast_5d3h(lat, lon)
        if not forecast:
            self.bot.send_message(message.chat.id, "❌ Не удалось получить прогноз. Попробуйте позже.")
            return
        
        # Группируем прогноз по дням
        from collections import defaultdict
        days_forecast = defaultdict(list)
        
        for item in forecast:
            dt_str = item.get('dt_txt', '')
            if dt_str:
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    day_key = dt.strftime("%Y-%m-%d")
                    days_forecast[day_key].append(item)
                except:
                    pass
        
        # Создаем inline-клавиатуру с днями
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        
        for i, (day_key, items) in enumerate(list(days_forecast.items())[:5], 1):
            try:
                dt = datetime.strptime(day_key, "%Y-%m-%d")
                day_name = day_names[dt.weekday()]
                emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][i-1]
                keyboard.add(types.InlineKeyboardButton(
                    f"{emoji} {day_name}",
                    callback_data=f"forecast_day_{day_key}"
                ))
            except:
                pass
        
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="forecast_back"))
        
        self.bot.send_message(
            message.chat.id,
            "📅 Выберите день для просмотра прогноза:",
            reply_markup=keyboard
        )
    
    def handle_callback(self, call):
        """Обработчик callback-запросов от inline-кнопок"""
        data = call.data
        user_id = call.from_user.id
        
        if data.startswith("forecast_day_"):
            day_key = data.replace("forecast_day_", "")
            self.handle_forecast_day(call, day_key)
        
        elif data == "forecast_back":
            self.bot.answer_callback_query(call.id)
            keyboard = self._get_main_menu_keyboard()
            self.bot.send_message(
                call.message.chat.id,
                "🏠 <b>Главное меню</b>\n\nВыберите действие:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        
        elif data == "back_to_menu":
            self.bot.answer_callback_query(call.id)
            keyboard = self._get_main_menu_keyboard()
            self.bot.send_message(
                call.message.chat.id,
                "🏠 <b>Главное меню</b>\n\nВыберите действие:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        
        elif data.startswith("notif_toggle_"):
            enabled = data.endswith("_on")
            self.handle_notification_toggle(call, enabled)
            # answer_callback_query уже вызывается в handle_notification_toggle
        
        elif data.startswith("notif_interval_"):
            interval = int(data.replace("notif_interval_", ""))
            self.handle_notification_interval(call, interval)
            # answer_callback_query уже вызывается в handle_notification_interval
        
        else:
            # Для остальных callback отвечаем здесь
            if data not in ["forecast_back", "back_to_menu"]:
                self.bot.answer_callback_query(call.id)
    
    def handle_forecast_day(self, call, day_key: str):
        """Обработчик выбора дня прогноза"""
        user_id = call.from_user.id
        user_data = self.storage.load_user(user_id)
        
        lat = user_data.get('lat')
        lon = user_data.get('lon')
        
        if not lat or not lon:
            self.bot.send_message(call.message.chat.id, "❌ Геолокация не найдена.")
            return
        
        forecast = self.weather_api.get_forecast_5d3h(lat, lon)
        if not forecast:
            self.bot.send_message(call.message.chat.id, "❌ Не удалось получить прогноз.")
            return
        
        # Фильтруем прогноз по дню
        day_items = []
        for item in forecast:
            dt_str = item.get('dt_txt', '')
            if dt_str and dt_str.startswith(day_key):
                day_items.append(item)
        
        if not day_items:
            self.bot.send_message(call.message.chat.id, "❌ Нет данных для этого дня.")
            return
        
        # Форматируем прогноз
        try:
            dt = datetime.strptime(day_key, "%Y-%m-%d")
            day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            day_name = f"{day_names[dt.weekday()]}, {dt.strftime('%d.%m.%Y')}"
        except:
            day_name = day_key
        
        forecast_text = format_forecast_day(day_items, day_name)
        
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад к дням", callback_data="forecast_back"))
        
        self.bot.edit_message_text(
            forecast_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboard
        )
    
    def handle_compare_request(self, message):
        """Обработчик запроса сравнения городов"""
        user_id = message.from_user.id
        self.user_states[user_id] = {'waiting_for_city1': True}
        keyboard = self._get_back_keyboard()
        self.bot.send_message(
            message.chat.id,
            "🌍 <b>Сравнить города</b>\n\n"
            "Введите название <b>первого города</b>:",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    
    def handle_compare_cities(self, message, city1: str, city2: str):
        """Сравнивает погоду в двух городах"""
        self.bot.send_message(message.chat.id, f"🔍 Сравниваю погоду в {city1} и {city2}...")
        
        # Получаем координаты городов
        coords1 = self.weather_api.get_coordinates(city1)
        coords2 = self.weather_api.get_coordinates(city2)
        
        if not coords1:
            self.bot.send_message(message.chat.id, f"❌ Город '{city1}' не найден.")
            return
        
        if not coords2:
            self.bot.send_message(message.chat.id, f"❌ Город '{city2}' не найден.")
            return
        
        # Получаем погоду
        weather1 = self.weather_api.get_current_weather(coords1[0], coords1[1])
        weather2 = self.weather_api.get_current_weather(coords2[0], coords2[1])
        
        if not weather1 or not weather2:
            self.bot.send_message(message.chat.id, "❌ Не удалось получить данные о погоде.")
            return
        
        # Форматируем сравнение
        temp1 = weather1.get('main', {}).get('temp', 'N/A')
        temp2 = weather2.get('main', {}).get('temp', 'N/A')
        desc1 = weather1.get('weather', [{}])[0].get('description_ru', 'N/A')
        desc2 = weather2.get('weather', [{}])[0].get('description_ru', 'N/A')
        humidity1 = weather1.get('main', {}).get('humidity', 'N/A')
        humidity2 = weather2.get('main', {}).get('humidity', 'N/A')
        wind1 = weather1.get('wind', {}).get('speed', 'N/A')
        wind2 = weather2.get('wind', {}).get('speed', 'N/A')
        
        # Получаем названия стран
        country1 = weather1.get('sys', {}).get('country', '')
        country2 = weather2.get('sys', {}).get('country', '')
        country1_ru = translate_country_code(country1)
        country2_ru = translate_country_code(country2)
        
        # Используем моноширинный шрифт для выравнивания
        comparison_text = (
            f"🌍 <b>Сравнение городов</b>\n\n"
            f"📍 <b>{city1}</b> ({country1_ru})\n"
            f"📍 <b>{city2}</b> ({country2_ru})\n\n"
            f"<code>"
            f"{'='*45}\n"
            f"Параметр          {city1[:12]:<12}  {city2[:12]}\n"
            f"{'='*45}\n"
            f"🌡️ Температура    {str(temp1) + '°C':<12}  {str(temp2) + '°C':<12}\n"
            f"☁️ Описание       {desc1[:12]:<12}  {desc2[:12]:<12}\n"
            f"💧 Влажность      {str(humidity1) + '%':<12}  {str(humidity2) + '%':<12}\n"
            f"💨 Ветер          {str(wind1) + ' м/с':<12}  {str(wind2) + ' м/с':<12}\n"
            f"</code>"
        )
        
        keyboard = self._get_back_keyboard()
        self.bot.send_message(message.chat.id, comparison_text, reply_markup=keyboard, parse_mode='HTML')
    
    def handle_extended_data_request(self, message):
        """Обработчик запроса расширенных данных"""
        user_id = message.from_user.id
        user_data = self.storage.load_user(user_id)
        
        # Используем последний запрошенный город/координаты, если есть
        lat = user_data.get('last_lat') or user_data.get('lat')
        lon = user_data.get('last_lon') or user_data.get('lon')
        city = user_data.get('last_city') or user_data.get('city', '')
        country_ru = user_data.get('last_country', '')
        
        if not lat or not lon:
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
            keyboard.add(
                types.KeyboardButton("📍 Отправить геолокацию", request_location=True),
                types.KeyboardButton("🏙️ Ввести город"),
                types.KeyboardButton("⬅️ Назад в меню")
            )
            self.bot.send_message(
                message.chat.id,
                "💨 <b>Расширенные данные</b>\n\n"
                "Для получения расширенных данных необходимо:\n"
                "• Сохранить геолокацию, или\n"
                "• Ввести название города\n\n"
                "Выберите способ:",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            self.user_states[user_id] = {'waiting_for_city': True, 'extended_mode': True}
            return
        
        keyboard = self._get_back_keyboard()
        self.bot.send_message(
            message.chat.id,
            f"🔍 Получаю расширенные данные для <b>{city}</b>...",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        # Получаем погоду и загрязнение воздуха
        weather = self.weather_api.get_current_weather(lat, lon)
        pollution_data = self.weather_api.get_air_pollution(lat, lon)
        
        if not weather:
            self.bot.send_message(message.chat.id, "❌ Не удалось получить данные о погоде.")
            return
        
        # Форматируем данные о погоде
        temp = weather.get('main', {}).get('temp', 'N/A')
        feels_like = weather.get('main', {}).get('feels_like', 'N/A')
        humidity = weather.get('main', {}).get('humidity', 'N/A')
        pressure = weather.get('main', {}).get('pressure', 'N/A')
        wind_speed = weather.get('wind', {}).get('speed', 'N/A')
        desc = weather.get('weather', [{}])[0].get('description_ru', 'N/A')
        
        # Получаем название страны
        country = weather.get('sys', {}).get('country', '')
        if not country_ru:
            country_ru = translate_country_code(country)
        
        # Конвертируем давление в мм.рт.ст.
        pressure_mmhg = convert_pressure_hpa_to_mmhg(pressure)
        pressure_text = f"{pressure_mmhg} мм.рт.ст." if pressure_mmhg != 'N/A' else "N/A"
        
        # Данные о солнце
        sunrise = weather.get('sys', {}).get('sunrise', 0)
        sunset = weather.get('sys', {}).get('sunset', 0)
        sunrise_str = datetime.fromtimestamp(sunrise).strftime("%H:%M") if sunrise else "N/A"
        sunset_str = datetime.fromtimestamp(sunset).strftime("%H:%M") if sunset else "N/A"
        
        message_text = (
            f"🌡️ <b>Погода + 💨 Качество воздуха</b>\n\n"
            f"📍 <b>{city}</b> ({country_ru})\n\n"
            f"<b>Погода:</b>\n\n"
            f"<b>Температура:</b> {temp}°C (ощущается {feels_like}°C)\n\n"
            f"☁️ <b>Описание:</b> {desc.capitalize()}\n\n"
            f"💧 <b>Влажность:</b> {humidity}%\n\n"
            f"📊 <b>Давление:</b> {pressure_text}\n\n"
            f"💨 <b>Ветер:</b> {wind_speed} м/с\n\n"
            f"🌅 <b>Восход:</b> {sunrise_str} | 🌇 <b>Закат:</b> {sunset_str}\n"
        )
        
        # Добавляем данные о загрязнении воздуха
        if pollution_data:
            components = pollution_data.get('components', {})
            analysis = self.weather_api.analyze_air_pollution(components, extended=True)
            
            message_text += (
                f"\n<b>Качество воздуха:</b>\n\n"
                f"<b>Статус:</b> {analysis.get('status_ru', 'Неизвестно')}\n\n"
                f"🌫️ <b>PM2.5:</b> {analysis.get('pm25', 0):.1f} µg/m³\n\n"
                f"🌫️ <b>PM10:</b> {analysis.get('pm10', 0):.1f} µg/m³"
            )
        else:
            message_text += "\n\n<i>Данные о качестве воздуха недоступны</i>"
        
        keyboard = self._get_back_keyboard()
        self.bot.send_message(message.chat.id, message_text, reply_markup=keyboard, parse_mode='HTML')
    
    def handle_notifications_menu(self, message):
        """Меню настроек уведомлений"""
        user_id = message.from_user.id
        user_data = self.storage.load_user(user_id)
        notifications = user_data.get('notifications', {})
        enabled = notifications.get('enabled', False)
        interval = notifications.get('interval_h', 2)
        last_sent = notifications.get('last_sent', '')
        
        status_text = "✅ <b>Включены</b>" if enabled else "❌ <b>Выключены</b>"
        last_sent_text = f"\nПоследнее уведомление: {last_sent}" if last_sent else ""
        
        menu_text = (
            f"🔔 <b>Настройки уведомлений</b>\n\n"
            f"Статус: {status_text}\n"
            f"Интервал: <b>{interval} часов</b>{last_sent_text}\n\n"
            f"<i>Выберите действие:</i>"
        )
        
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        
        if enabled:
            keyboard.add(types.InlineKeyboardButton("❌ Выключить", callback_data="notif_toggle_off"))
        else:
            keyboard.add(types.InlineKeyboardButton("✅ Включить", callback_data="notif_toggle_on"))
        
        keyboard.add(
            types.InlineKeyboardButton("1 ч", callback_data="notif_interval_1"),
            types.InlineKeyboardButton("2 ч", callback_data="notif_interval_2"),
            types.InlineKeyboardButton("3 ч", callback_data="notif_interval_3"),
            types.InlineKeyboardButton("6 ч", callback_data="notif_interval_6"),
            types.InlineKeyboardButton("12 ч", callback_data="notif_interval_12"),
            types.InlineKeyboardButton("24 ч", callback_data="notif_interval_24")
        )
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu"))
        
        reply_keyboard = self._get_back_keyboard()
        self.bot.send_message(message.chat.id, menu_text, reply_markup=reply_keyboard, parse_mode='HTML')
        self.bot.send_message(message.chat.id, "Настройте уведомления:", reply_markup=keyboard)
    
    def handle_notification_toggle(self, call, enabled: bool):
        """Переключение уведомлений"""
        user_id = call.from_user.id
        user_data = self.storage.load_user(user_id)
        interval = user_data.get('notifications', {}).get('interval_h', 2)
        
        self.storage.update_user_notification(user_id, enabled, interval)
        
        status = "включены" if enabled else "выключены"
        emoji = "✅" if enabled else "❌"
        self.bot.answer_callback_query(call.id, f"Уведомления {status}")
        
        # Обновляем inline-клавиатуру
        user_data = self.storage.load_user(user_id)
        notifications = user_data.get('notifications', {})
        enabled_new = notifications.get('enabled', False)
        interval_new = notifications.get('interval_h', 2)
        
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        if enabled_new:
            keyboard.add(types.InlineKeyboardButton("❌ Выключить", callback_data="notif_toggle_off"))
        else:
            keyboard.add(types.InlineKeyboardButton("✅ Включить", callback_data="notif_toggle_on"))
        
        keyboard.add(
            types.InlineKeyboardButton("1 ч", callback_data="notif_interval_1"),
            types.InlineKeyboardButton("2 ч", callback_data="notif_interval_2"),
            types.InlineKeyboardButton("3 ч", callback_data="notif_interval_3"),
            types.InlineKeyboardButton("6 ч", callback_data="notif_interval_6"),
            types.InlineKeyboardButton("12 ч", callback_data="notif_interval_12"),
            types.InlineKeyboardButton("24 ч", callback_data="notif_interval_24")
        )
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu"))
        
        try:
            self.bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboard
            )
        except:
            pass
    
    def handle_notification_interval(self, call, interval: int):
        """Установка интервала уведомлений"""
        user_id = call.from_user.id
        
        if not validate_notification_interval(interval):
            self.bot.answer_callback_query(call.id, "Некорректный интервал")
            self.bot.send_message(call.message.chat.id, "❌ Некорректный интервал (1-24 часа).")
            return
        
        user_data = self.storage.load_user(user_id)
        enabled = user_data.get('notifications', {}).get('enabled', False)
        old_interval = user_data.get('notifications', {}).get('interval_h', 2)
        
        self.storage.update_user_notification(user_id, enabled, interval)
        
        # Показываем понятное сообщение об изменении
        if old_interval != interval:
            interval_text = f"{interval} {'час' if interval == 1 else 'часа' if interval < 5 else 'часов'}"
            old_interval_text = f"{old_interval} {'час' if old_interval == 1 else 'часа' if old_interval < 5 else 'часов'}"
            self.bot.answer_callback_query(call.id, f"✅ Интервал изменен на {interval_text}")
            self.bot.send_message(
                call.message.chat.id,
                f"✅ <b>Интервал уведомлений изменен</b>\n\n"
                f"Было: {old_interval_text}\n"
                f"Стало: <b>{interval_text}</b>",
                parse_mode='HTML'
            )
        else:
            self.bot.answer_callback_query(call.id, f"Интервал уже установлен: {interval} ч")
        
        # Обновляем inline-клавиатуру
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        if enabled:
            keyboard.add(types.InlineKeyboardButton("❌ Выключить", callback_data="notif_toggle_off"))
        else:
            keyboard.add(types.InlineKeyboardButton("✅ Включить", callback_data="notif_toggle_on"))
        
        keyboard.add(
            types.InlineKeyboardButton("1 ч", callback_data="notif_interval_1"),
            types.InlineKeyboardButton("2 ч", callback_data="notif_interval_2"),
            types.InlineKeyboardButton("3 ч", callback_data="notif_interval_3"),
            types.InlineKeyboardButton("6 ч", callback_data="notif_interval_6"),
            types.InlineKeyboardButton("12 ч", callback_data="notif_interval_12"),
            types.InlineKeyboardButton("24 ч", callback_data="notif_interval_24")
        )
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu"))
        
        try:
            self.bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboard
            )
        except:
            pass
    
    def handle_inline_query(self, query):
        """Обработчик inline-запросов"""
        query_text = query.query.strip()
        
        if not query_text or len(query_text) < 2:
            return
        
        # Получаем координаты города
        coords = self.weather_api.get_coordinates(query_text)
        if not coords:
            return
        
        # Получаем текущую погоду
        weather = self.weather_api.get_current_weather(coords[0], coords[1])
        if not weather:
            return
        
        # Форматируем результат
        city_name = weather.get('name', query_text)
        temp = weather.get('main', {}).get('temp', 'N/A')
        desc = weather.get('weather', [{}])[0].get('description_ru', 'N/A')
        
        result_text = f"🌡️ {city_name}: {temp}°C, {desc.capitalize()}"
        
        # Создаем inline-результат
        result = types.InlineQueryResultArticle(
            id=str(hash(query_text)),
            title=f"Погода в {city_name}",
            description=f"{temp}°C, {desc}",
            input_message_content=types.InputTextMessageContent(
                message_text=result_text
            )
        )
        
        try:
            self.bot.answer_inline_query(query.id, [result], cache_time=300)
        except Exception as e:
            logger.error(f"Ошибка обработки inline-запроса: {e}")
    
    def run(self):
        """Запускает бота"""
        logger.info("Запуск бота...")
        try:
            # Удаляем webhook если он активен (для работы polling)
            try:
                self.bot.delete_webhook(drop_pending_updates=True)
                logger.info("Webhook удален (если был активен)")
            except Exception as e:
                logger.warning(f"Ошибка при удалении webhook (возможно, его не было): {e}")
            
            self.bot.polling(none_stop=True, interval=0)
        except Exception as e:
            logger.error(f"Ошибка при работе бота: {e}")
            raise


if __name__ == "__main__":
    try:
        bot = WeatherBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

