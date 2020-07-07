"""
Скрипт генерирует промокоды для периода времени.
При наступлении каждого розыгрыша сохраняет инфу о розыгрыше в бд mysql
и постит промокод в группу на стену.

Для использования необходимо:
0. Чтобы не было проблем надо использовать питон 3.7+
1. Должна быть установлена база mysql
2. Создать venv:
    python -m venv venv
3. Активировать venv
    windows: venv\ Scripts\ activate
    linux: source venv/ bin/ activate
4. Обновить pip
    python -m pip install --upgrade pip
5. Установить зависимости
    pip install -r requirements.txt
6. Создать standalone приложение vk по ссылке:
    https://vk.com/editapp?act=create.
    У созданного приложения есть два состояния: включено и выключено.
    Я оставил выключенным, и скрипт работал. Скорее всего включенное
    состояние - это доступ пользоватей вк к нему. Он нам не нужен.
7. Получить токен пользователя:
    Сделать запрос (предварительно залогинившись в вк от пользователя,
        который может постить на стену и смотреть фото целевой группы):
    https://oauth.vk.com/authorize?client_id=<VK_STANDALONE_APP_ID>&display=page&redirect_uri=https://oauth.vk.com/blank.html&scope=wall,photos,offline&response_type=token&v=5.52
    заменив <VK_STANDALONE_APP_ID> на id созданного вк приложения и заменив версию
    апи v=5.52, если она окажется устарелой
8. Заполнить технические константы в соответствующем region ниже в скрипте.
9. Определить пользовательские константы в соответствующем region.
10. Запустить скрипт
    python .\script.py
    Ну или повесить в какой-нибудь systemd

https://vk.com/fixed1995 https://github.com/fx-it/
"""

import pymysql.cursors
import time
import random
from math import ceil, floor
import string
import requests
import uuid
import pathlib
import datetime


# region технические константы

DB_HOST = TODO
DB_USER = TODO
DB_PASSWORD = TODO
DB_NAME = TODO

VK_STANDALONE_APP_ID = TODO
"""
Число.
ID standalone приложения vk, использующее скрипт.
В данный момент в приложении не используется,
но указывается для удобства.
"""

GROUP_ID = TODO
"""
Число.
ID группы, с которой будет работать скрипт.
"""

VK_ACCESS_TOKEN = TODO
"""
Строка.
Токен для standalone вк приложения от пользователя
(не от группы, ибо методы, используемые в программе, требует токен пользователя),
у которого есть право писать на стене сообщества.
"""

VK_API_VERSION = 5.21
"""Версия api vk"""

# endregion

# region константы для изменения пользователями

CYCLE_SECONDS = 86400
"""
Количество секунд в котором будет разыгрываться голда.
Т.е. один круг розыгрышей.
3600 - 1 час
86400 - сутки
604800 - неделя
"""

CYCLE_GOLD_COUNT = 60
"""
Количество голды, которое будет разыграно за период времени
"""

PROMO_CODE_COUNT = 3
"""
Количество розыгрышей в цикле розыгрышей
"""

MAX_GOLD_VALUE_INCREASING_PERCENT = 30
"""
Максимальный процент (от 0 до 100) отклонения голды в возрастание, которое будет
разыграно в каждом розыгрыше, исходя от среднего значения голды.
Например, если указано, что в каждом розыгрыше разыгрывается 10 голды,
и значение этой константы = 20,
то разыгрываться будет 100%-120%, т.е. или 10 или 11 или 12 голды.

Работает в паре с MAX_GOLD_VALUE_DECREASING_PERCENT.
"""

MAX_GOLD_VALUE_DECREASING_PERCENT = 30
"""
Максимальный процент (от 0 до 100) отклонения голды в убывание, которое будет
разыграно в каждом розыгрыше, исходя от среднего значения голды.
Например, если указано, что в каждом розыгрыше разыгрывается 10 голды,
и значение этой константы = 20,
то разыгрываться будет 80%-100%, т.е. или 8 или 9 или 10 голды.

Работает в паре с MAX_GOLD_VALUE_INCREASING_PERCENT.
"""

JACKPOT_PERCENT = 3
"""
Процент (от 0 до 100), по которому может выпасть джекпот.
Джекпот не входит в CYCLE_GOLD_COUNT.
Т.е. разыгранное число голды за цикл розыгрышей окажется больше.
Чтобы отключить, впиши 0.
"""

JACKPOT_GOLD_COUNT = 30
"""
Количество голды-джекпота, которое добавляется к обычному выигрышу.
"""

ALPHANUMERIC_PROMO_CODE_LENGTH = 8
"""
Длина букво-численного промокода
"""

# endregion

# region программные константы
_THIS_SCRIPT_PARENT_PATH = str(pathlib.Path(__file__).parent.absolute())
LOG_FILE_PATH = _THIS_SCRIPT_PARENT_PATH + '\log.txt'
PROMO_CODE_IMAGE_PATH = _THIS_SCRIPT_PARENT_PATH + '\promo_code.jpg'

ENCODING = 'utf8'

MIDDLE_GOLD_VALUE_FOR_A_RAFFLE = CYCLE_GOLD_COUNT//PROMO_CODE_COUNT
"""Среднее целое значение одного розыгрыша"""

GOLD_MIX_COUNT = PROMO_CODE_COUNT * 5
"""
Сколько раз будут изменятся значения голдов попарно, чтобы
добиться некоторого распределения значений.
Степень распределения зависит от
констант MAX_GOLD_VALUE_INCREASING_PERCENT и MAX_GOLD_VALUE_DECREASING_PERCENT
"""

# endregion


db_connection = pymysql.connect(host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    db=DB_NAME,
    use_unicode = True,
    charset = ENCODING,
    cursorclass=pymysql.cursors.DictCursor)


class Gold(object):

    INITIAL_VALUE = MIDDLE_GOLD_VALUE_FOR_A_RAFFLE

    _MAX_INCREASING_VALUE = INITIAL_VALUE * (100+MAX_GOLD_VALUE_INCREASING_PERCENT) / 100
    MAX_INCREASING_VALUE = floor(_MAX_INCREASING_VALUE) if _MAX_INCREASING_VALUE > 0 else ceil(_MAX_INCREASING_VALUE)

    _MAX_DECREASING_VALUE = INITIAL_VALUE * (100-MAX_GOLD_VALUE_DECREASING_PERCENT) / 100
    MAX_DECREASING_VALUE = ceil(_MAX_DECREASING_VALUE) if _MAX_DECREASING_VALUE > 0 else floor(_MAX_DECREASING_VALUE)

    def __init__(self):
        super().__init__()
        self._current_value = self.__class__.INITIAL_VALUE
        self._promo_code = None

    @property
    def current_value(self):
        return self._current_value

    @current_value.setter
    def current_value(self, value):
        self._current_value = value

    @property
    def promo_code(self):
        return self._promo_code

    @promo_code.setter
    def promo_code(self, value):
        self._promo_code = value

    @classmethod
    def change_values(cls, first_gold_value: 'Gold', second_gold_value: 'Gold', value: int) -> None:
        """
        Функция изменяет количество голда между двумя объектами голда соответственно друг другу.
        Т.е. если от одного объекта голда вычлось некоторая сумма, то она будет добавлена
        ко второму объекту голды, чтобы необходимоей количество голды за весь цикл розыгрышей
        не изменилось
        """
        if cls.can_be_changed(first_gold_value, second_gold_value, value):
            cls._change_values(first_gold_value, second_gold_value, value)
        else:
            # попадаем сюда, если изменение значений голдов с текущим value
            #  перевалит за их допустимые границы. Тогда уменьшим количество
            # самых низких и самых высоких значений,
            # отнимая 1 из самого большого и прибавляя 1 для самого маленького (если они являются таковыми)
            should_change = False
            if first_gold_value.current_value == cls.MAX_INCREASING_VALUE \
                    and second_gold_value.current_value == cls.MAX_DECREASING_VALUE:
                value = -1
                should_change = True
            elif first_gold_value.current_value == cls.MAX_DECREASING_VALUE \
                    and second_gold_value.current_value == cls.MAX_INCREASING_VALUE:
                value = 1
                should_change = True

            if should_change:
                cls._change_values(first_gold_value, second_gold_value, value)

    @classmethod
    def _change_values(cls, first_gold_value: 'Gold', second_gold_value: 'Gold', value: int):
        """Простой обмен значений value-количества голды между двумя объектами голды"""
        first_gold_value.current_value = first_gold_value.current_value + value
        second_gold_value.current_value = second_gold_value.current_value - value

    @classmethod
    def can_be_changed(cls, first_gold_value: 'Gold', second_gold_value: 'Gold', value: int) -> bool:
        """
        Проверка, не перейдут ли значения голдов свои пороги 
        MAX_INCREASING_VALUE и MAX_DECREASING_VALUE после изменений
        """
        if value > 0:
            if (
                    first_gold_value.current_value + value > cls.MAX_INCREASING_VALUE \
                    or first_gold_value.current_value - value < cls.MAX_DECREASING_VALUE \
                    or second_gold_value.current_value + value > cls.MAX_INCREASING_VALUE \
                    or second_gold_value.current_value - value < cls.MAX_DECREASING_VALUE
                ):
                return False
            else:
                return True
        else:
            if (
                    first_gold_value.current_value - value > cls.MAX_INCREASING_VALUE \
                    or first_gold_value.current_value + value < cls.MAX_DECREASING_VALUE \
                    or second_gold_value.current_value - value > cls.MAX_INCREASING_VALUE \
                    or second_gold_value.current_value + value < cls.MAX_DECREASING_VALUE
                ):
                return False
            else:
                return True

    @classmethod
    def get_values_to_change(cls) -> list:
        """
        Функция возвращает список чисел (исходя из MAX_DECREASING_VALUE и MAX_INCREASING_VALUE),
        на которые объекты голдов могут обменяться значениями голдов
        """
        change_values = set()
        for value in range(cls.MAX_DECREASING_VALUE, cls.MAX_INCREASING_VALUE+1):
            change_values.add(cls.INITIAL_VALUE - value)
        
        try:
            change_values.remove(0)
        except ValueError:
            pass

        change_values = [0-value for value in change_values]

        return change_values

    def __str__(self):
        if self.promo_code:
            return self.promo_code+"."+str(self.current_value)
        else:
            return "."+str(self.current_value)

    def __repr__(self):
        return self.__str__()


def generate_promo_code() -> str:
    """Генерирует и возвращает новый промокод"""
    alphanumerics = string.ascii_uppercase + string.digits
    promo_code = [random.choice(alphanumerics) for _ in range(0, ALPHANUMERIC_PROMO_CODE_LENGTH)]
    return ''.join(promo_code)

def attach_promo_codes(golds_list: 'list of Gold') -> None:
    """
    Прикрепляет новые промо-коды к объектам голдов из списка в параметре
    """
    with db_connection:
        get_all_promo_codes_sql = "SELECT `code` FROM `promo_codes`"
        with db_connection.cursor() as cursor:
            cursor.execute(get_all_promo_codes_sql)
            exist_promo_codes_dicts = cursor.fetchall()

    exist_promo_codes = [code['code'] for code in exist_promo_codes_dicts]

    for gold_obj in golds_list:
        new_promo_code = generate_promo_code()
        while new_promo_code in exist_promo_codes:
            new_promo_code = generate_promo_code()
        gold_obj.promo_code = new_promo_code

def get_cycle_golds_for_raffle() -> list:
    """
    Функция формирует список голдов, доступных для розыгрыша
    в цикле розыгрышей. Притом, значения голдов в списке распределяются так,
    чтобы в розыгрышах было примерно разное количество голды.
    """
    cycle_golds_for_raffle = [Gold() for _ in range(0, PROMO_CODE_COUNT)]
    cycle_golds_for_raffle_last_idx = len(cycle_golds_for_raffle) - 1

    values_to_change = Gold.get_values_to_change()
    for i in range(0,GOLD_MIX_COUNT):
        first_change_idx,second_change_idx = random.randint(0, cycle_golds_for_raffle_last_idx),random.randint(0, cycle_golds_for_raffle_last_idx)
        if first_change_idx == second_change_idx:
            continue
        else:
            Gold.change_values(
                cycle_golds_for_raffle[first_change_idx],
                cycle_golds_for_raffle[second_change_idx],
                random.choice(values_to_change))

    attach_promo_codes(cycle_golds_for_raffle)

    return cycle_golds_for_raffle

def is_it_jackpot() -> bool:
    """Функция определяет джекпот или нет"""
    if JACKPOT_PERCENT == 0:
        return False
    
    hundred_random = random.choice(list(range(1,101)))
    if hundred_random - JACKPOT_PERCENT <= 0:
        return True
    else:
        return False

def main_program_cycle():

    cycle_promo_code_counter = 0
    """Счетчик розыгрышей для цикла розыгрышей"""
    cycle_golds_for_raffle = []
    """Список объектов голдов для розыгрышей"""
    waiting_seconds_for_raffles = []
    """Список секунд, сколько ждать между розыгрышами"""

    # получение картинки промокода
    promo_code_upload_server = requests.get(
        url=f'https://api.vk.com/method/photos.getWallUploadServer?group_id={GROUP_ID}&access_token={VK_ACCESS_TOKEN}&v={VK_API_VERSION}')
    photo_upload_url = promo_code_upload_server.json()['response']['upload_url']
    promo_code_img_file = open(PROMO_CODE_IMAGE_PATH, 'rb')
    response_post_upload_url = requests.post(photo_upload_url, files={'file': promo_code_img_file}).json()
    promo_code_img_file.close()
    response_save_wall_photo = requests.get(
        url=f'https://api.vk.com/method/photos.saveWallPhoto?group_id={GROUP_ID}&server={response_post_upload_url["server"]}&photo={response_post_upload_url["photo"]}&hash={response_post_upload_url["hash"]}&access_token={VK_ACCESS_TOKEN}&v={VK_API_VERSION}')
    response_save_wall_photo_json = response_save_wall_photo.json()['response']

    emoji_rupor = "&#128227;"
    emoji_molniya = "&#9889;"
    emoji_voskl_znak = "&#10071;"
    emoji_jackpot = "&#127920;"

    cycle_couter = 0
    """Счетчик циклов розыгрышей"""

    while True:
        
        if not cycle_golds_for_raffle and not waiting_seconds_for_raffles:
            """Начинается новый цикл розыгрышей"""
            cycle_couter = cycle_couter + 1

            # залогируем новый круг
            with open(LOG_FILE_PATH, 'a', encoding=ENCODING) as log_file:
                log_file.write(f"Новый круг {cycle_couter}\n")

            # определить значения голдов для розыгрыша
            cycle_golds_for_raffle = get_cycle_golds_for_raffle()

            # определить время для каждого из розыгрышей
            waiting_seconds_for_raffles = []
            seconds_for_raffle = []
            """
            Хранит секунды, когда делать розыгрыш.
            Используется для заполнения waiting_seconds_for_raffles
            """

            for _ in range(0, PROMO_CODE_COUNT):
                second_for_raffle = random.randrange(start=1, stop=CYCLE_SECONDS+1, step=1)
                while second_for_raffle in seconds_for_raffle:
                    second_for_raffle = random.randrange(start=1, stop=CYCLE_SECONDS+1, step=1)
                seconds_for_raffle.append(second_for_raffle)
            
            seconds_for_raffle_len = len(seconds_for_raffle)
            seconds_for_raffle_sorted = sorted(seconds_for_raffle)

            if seconds_for_raffle_sorted[-1] != CYCLE_SECONDS:
                """
                Для того, чтобы время цикла розыгрышей прошло полностью,
                то добавим максимальное значение секунды цикла розыгрыша,
                если оно не оказалось секундой последнего розыгрыша
                """
                seconds_for_raffle_sorted.append(CYCLE_SECONDS)

            for idx,second_moment in enumerate(seconds_for_raffle_sorted):
                if idx==0:
                    waiting_seconds_for_raffles.append(second_moment)
                else:
                    waiting_seconds_for_raffles.append(second_moment-seconds_for_raffle_sorted[idx-1])
        
        # ждем момента, когда пора разыгрывать
        time.sleep(waiting_seconds_for_raffles.pop(0))

        if not cycle_golds_for_raffle:
            """
            Попадаем сюда, если голда для розыгрыша в этом круге уже нет,
            но нужно было дождаться полного завершения круга по времени
            и начать новый круг, не разыгрывая ничего.
            """
            continue

        # получим объект голды для этого розыгрыша
        gold = cycle_golds_for_raffle.pop(0)

        # попробуем добавить джекпот
        is_jackpot = is_it_jackpot()
        if is_jackpot:
            gold.current_value = gold.current_value + JACKPOT_GOLD_COUNT

        # залогируем текущий голд для розыгрыша
        with open(LOG_FILE_PATH, 'a', encoding=ENCODING) as log_file:
            log_file.write(f"gold{' джекпот' if is_jackpot else ''}: {gold}\n")

        # добавим промокод в базу
        with db_connection:
            sql = "INSERT INTO `promo_codes` (`code`, `bonus_money_gold`, `bonus_item_data`, `desc`) " + \
                f"VALUES ('{gold.promo_code}', {gold.current_value}, '', 'Промокодик для паблоса в вк')"
            with db_connection.cursor() as cursor:
                cursor.execute(sql)
                db_connection.commit()

        # публикация поста в вк
        message = f"{emoji_rupor} Доброго времени суток, уважаемые игроки!\n\n{emoji_molniya} " + \
            f"Промокод {'(ДЖЕКПОТ ' + '%s' % emoji_jackpot + ')' if is_jackpot else ''}: {gold.promo_code} ({gold.current_value} золотых монет)\n\n{emoji_voskl_znak} " + \
            "Напоминаем! Бонус за использование " + \
            "данного промокода начисляется первому, кто успел его активировать."
        attachments = f"photo{response_save_wall_photo_json[0]['owner_id']}_{response_save_wall_photo_json[0]['id']}"
        wall_post = requests.post(
            url=f'https://api.vk.com/method/wall.post?access_token={VK_ACCESS_TOKEN}&v={VK_API_VERSION}',
            data={'owner_id': -GROUP_ID, 'message': message, "attachments": attachments, 'guid': uuid.uuid4().hex, 'from_group': 1})


if __name__ == '__main__':
    main_program_cycle()
