"""
Модуль для работы с хранением данных пользователей
"""
import json
import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_FILE = "User_Data.json"


class UserStorage:
    """Класс для работы с хранением данных пользователей в JSON"""
    
    def __init__(self, data_file: str = DEFAULT_DATA_FILE):
        """
        Инициализация хранилища
        
        Args:
            data_file: Путь к файлу с данными
        """
        self.data_file = data_file
        self._ensure_file_exists()
    
    def _ensure_file_exists(self) -> None:
        """Создает файл с пустым словарем если его нет"""
        if not os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.info(f"Создан файл {self.data_file}")
            except Exception as e:
                logger.error(f"Ошибка создания файла {self.data_file}: {e}")
    
    def _load_data(self) -> Dict[str, Any]:
        """
        Загружает все данные из файла
        
        Returns:
            Словарь со всеми данными пользователей
        """
        try:
            if not os.path.exists(self.data_file):
                return {}
            
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON в {self.data_file}: {e}")
            # Пытаемся восстановить файл
            self._backup_and_reset()
            return {}
        
        except Exception as e:
            logger.error(f"Ошибка чтения {self.data_file}: {e}")
            return {}
    
    def _save_data(self, data: Dict[str, Any]) -> bool:
        """
        Сохраняет данные в файл
        
        Args:
            data: Словарь с данными для сохранения
        
        Returns:
            True если успешно, False иначе
        """
        try:
            # Создаем резервную копию перед записью
            if os.path.exists(self.data_file):
                backup_file = f"{self.data_file}.backup"
                try:
                    with open(self.data_file, 'r', encoding='utf-8') as src:
                        with open(backup_file, 'w', encoding='utf-8') as dst:
                            dst.write(src.read())
                except:
                    pass
            
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Данные сохранены в {self.data_file}")
            return True
        
        except Exception as e:
            logger.error(f"Ошибка сохранения в {self.data_file}: {e}")
            return False
    
    def _backup_and_reset(self) -> None:
        """Создает резервную копию поврежденного файла и сбрасывает его"""
        if os.path.exists(self.data_file):
            backup_file = f"{self.data_file}.corrupted"
            try:
                os.rename(self.data_file, backup_file)
                logger.warning(f"Поврежденный файл переименован в {backup_file}")
            except:
                pass
        
        self._ensure_file_exists()
    
    def load_user(self, user_id: int) -> Dict[str, Any]:
        """
        Загружает данные пользователя
        
        Args:
            user_id: ID пользователя Telegram
        
        Returns:
            Словарь с данными пользователя или пустой словарь
        """
        all_data = self._load_data()
        user_key = str(user_id)
        
        if user_key in all_data:
            logger.info(f"Загружены данные пользователя {user_id}")
            return all_data[user_key]
        
        logger.info(f"Пользователь {user_id} не найден, возвращаем пустые данные")
        return {}
    
    def save_user(self, user_id: int, data: Dict[str, Any]) -> bool:
        """
        Сохраняет данные пользователя
        
        Args:
            user_id: ID пользователя Telegram
            data: Словарь с данными для сохранения
        
        Returns:
            True если успешно, False иначе
        """
        all_data = self._load_data()
        user_key = str(user_id)
        
        # Объединяем существующие данные с новыми
        if user_key in all_data:
            all_data[user_key].update(data)
        else:
            all_data[user_key] = data
        
        success = self._save_data(all_data)
        
        if success:
            logger.info(f"Сохранены данные пользователя {user_id}")
        
        return success
    
    def load_all(self) -> Dict[str, Any]:
        """
        Загружает данные всех пользователей
        
        Returns:
            Словарь со всеми пользователями
        """
        return self._load_data()
    
    def update_user_notification(self, user_id: int, enabled: bool, interval_h: int, last_sent: Optional[str] = None) -> bool:
        """
        Обновляет настройки уведомлений пользователя
        
        Args:
            user_id: ID пользователя
            enabled: Включены ли уведомления
            interval_h: Интервал в часах
            last_sent: Время последней отправки (опционально)
        
        Returns:
            True если успешно
        """
        user_data = self.load_user(user_id)
        
        if 'notifications' not in user_data:
            user_data['notifications'] = {}
        
        user_data['notifications']['enabled'] = enabled
        user_data['notifications']['interval_h'] = interval_h
        
        if last_sent:
            user_data['notifications']['last_sent'] = last_sent
        
        return self.save_user(user_id, user_data)
    
    def update_user_location(self, user_id: int, city: str, lat: float, lon: float) -> bool:
        """
        Обновляет геолокацию пользователя
        
        Args:
            user_id: ID пользователя
            city: Название города
            lat: Широта
            lon: Долгота
        
        Returns:
            True если успешно
        """
        user_data = self.load_user(user_id)
        user_data['city'] = city
        user_data['lat'] = lat
        user_data['lon'] = lon
        
        return self.save_user(user_id, user_data)
    
    def get_user_location(self, user_id: int) -> Optional[tuple]:
        """
        Получает геолокацию пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Кортеж (lat, lon) или None если нет данных
        """
        user_data = self.load_user(user_id)
        
        if 'lat' in user_data and 'lon' in user_data:
            return (user_data['lat'], user_data['lon'])
        
        return None

