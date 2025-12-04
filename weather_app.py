"""
Модуль для работы с OpenWeather API
"""
import requests
import logging
from typing import Optional, Tuple, Dict, List, Any
from utils import load_cache, save_cache, retry_request, translate_weather_description

logger = logging.getLogger(__name__)


class WeatherAPI:
    """Класс для работы с OpenWeather API"""
    
    BASE_URL = "https://api.openweathermap.org"
    GEOCODING_URL = f"{BASE_URL}/geo/1.0/direct"
    CURRENT_WEATHER_URL = f"{BASE_URL}/data/2.5/weather"
    FORECAST_URL = f"{BASE_URL}/data/2.5/forecast"
    AIR_POLLUTION_URL = f"{BASE_URL}/data/2.5/air_pollution"
    
    def __init__(self, api_key: str):
        """
        Инициализация API
        
        Args:
            api_key: API ключ от OpenWeatherMap
        """
        self.api_key = api_key
        if not api_key or api_key == "your_openweather_key":
            logger.warning("API ключ не установлен или использует значение по умолчанию")
    
    def _make_request(self, url: str, params: Dict[str, Any], endpoint_name: str, 
                     use_cache: bool = True, lat: Optional[float] = None, 
                     lon: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Выполняет HTTP запрос с кэшированием и retry логикой
        
        Args:
            url: URL для запроса
            params: Параметры запроса
            endpoint_name: Название endpoint для кэша
            use_cache: Использовать ли кэш
            lat: Широта для ключа кэша
            lon: Долгота для ключа кэша
        
        Returns:
            JSON ответ или None при ошибке
        """
        # Проверяем кэш если есть координаты
        cache_key = None
        if use_cache and lat is not None and lon is not None:
            cache_key = f"{lat}_{lon}_{endpoint_name}"
            cached_data = load_cache(cache_key)
            if cached_data:
                return cached_data
        
        # Добавляем API ключ и язык
        params['appid'] = self.api_key
        params['lang'] = 'ru'
        
        def _request():
            """Внутренняя функция для retry"""
            try:
                response = requests.get(url, params=params, timeout=10)
                
                # Обработка rate limit
                if response.status_code == 429:
                    logger.warning("Rate limit достигнут, будет повторная попытка")
                    raise requests.RequestException("Rate limit")
                
                response.raise_for_status()
                return response.json()
            
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Ресурс не найден: {url}")
                    return None
                elif e.response.status_code == 401:
                    logger.error("Неверный API ключ")
                    return None
                raise
            
            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка запроса: {e}")
                raise
        
        # Выполняем запрос с retry
        result = retry_request(_request, max_attempts=3)
        
        # Сохраняем в кэш если успешно
        if result and cache_key:
            save_cache(cache_key, result)
        
        return result
    
    def get_coordinates(self, city: str, limit: int = 1) -> Optional[Tuple[float, float]]:
        """
        Получает координаты города через геокодинг
        
        Args:
            city: Название города
            limit: Максимальное количество результатов
        
        Returns:
            Кортеж (lat, lon) или None если город не найден
        """
        if not city or len(city.strip()) < 2:
            logger.warning(f"Некорректное название города: {city}")
            return None
        
        params = {
            'q': city,
            'limit': limit
        }
        
        try:
            response = self._make_request(
                self.GEOCODING_URL, 
                params, 
                'geocoding',
                use_cache=False  # Геокодинг не кэшируем по координатам
            )
            
            if not response or not isinstance(response, list) or len(response) == 0:
                logger.info(f"Город '{city}' не найден")
                return None
            
            location = response[0]
            lat = location.get('lat')
            lon = location.get('lon')
            
            if lat is None or lon is None:
                logger.warning(f"Координаты не найдены для города '{city}'")
                return None
            
            logger.info(f"Найдены координаты для '{city}': ({lat}, {lon})")
            return (float(lat), float(lon))
        
        except Exception as e:
            logger.error(f"Ошибка получения координат для '{city}': {e}")
            return None
    
    def get_current_weather(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """
        Получает текущую погоду по координатам
        
        Args:
            lat: Широта
            lon: Долгота
        
        Returns:
            Словарь с данными погоды или None при ошибке
        """
        params = {
            'lat': lat,
            'lon': lon,
            'units': 'metric'
        }
        
        try:
            data = self._make_request(
                self.CURRENT_WEATHER_URL,
                params,
                'weather',
                use_cache=True,
                lat=lat,
                lon=lon
            )
            
            if data:
                # Переводим описание на русский если нужно
                if 'weather' in data and len(data['weather']) > 0:
                    desc = data['weather'][0].get('description', '')
                    if desc:
                        data['weather'][0]['description_ru'] = translate_weather_description(desc)
            
            return data
        
        except Exception as e:
            logger.error(f"Ошибка получения текущей погоды: {e}")
            return None
    
    def get_forecast_5d3h(self, lat: float, lon: float) -> List[Dict[str, Any]]:
        """
        Получает 5-дневный прогноз с шагом 3 часа
        
        Args:
            lat: Широта
            lon: Долгота
        
        Returns:
            Список словарей с прогнозом или пустой список при ошибке
        """
        params = {
            'lat': lat,
            'lon': lon,
            'units': 'metric'
        }
        
        try:
            data = self._make_request(
                self.FORECAST_URL,
                params,
                'forecast',
                use_cache=True,
                lat=lat,
                lon=lon
            )
            
            if not data or 'list' not in data:
                logger.warning("Прогноз не получен или пуст")
                return []
            
            forecast_list = data['list']
            
            # Переводим описания на русский
            for item in forecast_list:
                if 'weather' in item and len(item['weather']) > 0:
                    desc = item['weather'][0].get('description', '')
                    if desc:
                        item['weather'][0]['description_ru'] = translate_weather_description(desc)
            
            logger.info(f"Получен прогноз на {len(forecast_list)} периодов")
            return forecast_list
        
        except Exception as e:
            logger.error(f"Ошибка получения прогноза: {e}")
            return []
    
    def get_air_pollution(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """
        Получает данные о загрязнении воздуха
        
        Args:
            lat: Широта
            lon: Долгота
        
        Returns:
            Словарь с данными загрязнения или None при ошибке
        """
        params = {
            'lat': lat,
            'lon': lon
        }
        
        try:
            data = self._make_request(
                self.AIR_POLLUTION_URL,
                params,
                'air_pollution',
                use_cache=True,
                lat=lat,
                lon=lon
            )
            
            if not data or 'list' not in data or len(data['list']) == 0:
                logger.warning("Данные о загрязнении воздуха не получены")
                return None
            
            # Возвращаем первый элемент (текущие данные)
            pollution_data = data['list'][0]
            
            logger.info("Получены данные о загрязнении воздуха")
            return pollution_data
        
        except Exception as e:
            logger.error(f"Ошибка получения данных о загрязнении: {e}")
            return None
    
    def analyze_air_pollution(self, components: Dict[str, float], extended: bool = False) -> Dict[str, Any]:
        """
        Анализирует качество воздуха на основе компонентов
        
        Args:
            components: Словарь с компонентами (co, no, no2, o3, so2, pm2_5, pm10, nh3)
            extended: Включать ли детальную информацию
        
        Returns:
            Словарь с анализом качества воздуха
        """
        from utils import translate_pollution_status
        
        if not components:
            return {
                'status': 'Неизвестно',
                'status_ru': 'Неизвестно',
                'aqi': 0
            }
        
        # Получаем AQI (Air Quality Index) из компонентов
        # Используем упрощенную логику на основе PM2.5 и PM10
        pm25 = components.get('pm2_5', 0)
        pm10 = components.get('pm10', 0)
        
        # Определяем статус на основе PM2.5 (основной индикатор)
        if pm25 <= 12:
            status = "Good"
            aqi = 1
        elif pm25 <= 35:
            status = "Fair"
            aqi = 2
        elif pm25 <= 55:
            status = "Moderate"
            aqi = 3
        elif pm25 <= 150:
            status = "Poor"
            aqi = 4
        else:
            status = "Very Poor"
            aqi = 5
        
        result = {
            'status': status,
            'status_ru': translate_pollution_status(status),
            'aqi': aqi,
            'pm25': pm25,
            'pm10': pm10
        }
        
        if extended:
            result['components'] = {
                'co': components.get('co', 0),
                'no': components.get('no', 0),
                'no2': components.get('no2', 0),
                'o3': components.get('o3', 0),
                'so2': components.get('so2', 0),
                'pm2_5': components.get('pm2_5', 0),
                'pm10': components.get('pm10', 0),
                'nh3': components.get('nh3', 0)
            }
        
        return result

