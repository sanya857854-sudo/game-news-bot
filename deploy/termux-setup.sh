#!/data/data/com.termux/files/usr/bin/bash
# Установка бота игровых новостей в Termux (Android).
# Запускать из папки проекта:   bash deploy/termux-setup.sh
#
# Нужен Android 7 или новее. Termux ставится из F-Droid или с github.com/termux/termux-app
# (версия из Google Play — экспериментальная, лучше её не использовать).
set -e

echo "== Обновляю пакеты Termux =="
pkg update -y && pkg upgrade -y

echo "== Ставлю Python, ffmpeg, картинки и утилиты Termux =="
# python-pillow ставим пакетом Termux — иначе pip будет долго и нудно собирать его сам
pkg install -y python python-pillow ffmpeg termux-api

echo "== Ставлю библиотеки проекта =="
pip install --upgrade pip
pip install requests python-dotenv tzdata

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан файл .env из образца — впишите в него токен и канал."
fi

cat <<'ПОДСКАЗКА'

== Дальше вручную ==

1) Впишите токен и канал:
     nano .env          # TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID; выход: Ctrl+X, затем Y, затем Enter

2) Проверьте настройки:
     python main.py --check

3) Запустите бота так, чтобы он пережил выключение экрана:
     termux-wake-lock
     mkdir -p logs
     nohup python main.py >> logs/termux.log 2>&1 &

   Остановить:   pkill -f "python main.py"

   Команда termux-wake-lock работает, только если рядом с Termux установлен
   плагин Termux:API (из F-Droid). Без него просто держите телефон на зарядке
   и отключите для Termux оптимизацию батареи (пункт 4).

4) Чтобы Android не убивал Termux в фоне:
     - разрешите Termux работу в фоне (Настройки → Приложения → Termux → Батарея → «Без ограничений»);
     - на Android 12+ включите в «Для разработчиков» пункт «Отключить ограничения дочерних процессов».
   Подробности: https://dontkillmyapp.com/

5) Чтобы бот поднимался после перезагрузки телефона — поставьте Termux:Boot
   (из F-Droid) и добавьте в ~/.termux/boot/ файл с теми же командами из пункта 3.

Телефону нужен Wi-Fi и зарядка. Советы для слабого телефона — в README.md.
ПОДСКАЗКА
