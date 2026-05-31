# Вход в систему по биометрии (urblock_po)

## Где происходит вход (для пользователя)

**Биометрия работает только в графическом интерфейсе**, не в терминале:

| Место | Что видит пользователь |
|-------|-------------------------|
| Экран входа GDM | Окно Urblock справа сверху + поле пароля GDM |
| Экран блокировки (Win+L) | То же окно; при успехе — разблокировка без ввода в TTY |
| Urblock GUI | Только **регистрация** лица (образцы в камеру) |

Терминальные команды ниже — **только для администратора** (установка, отладка).  
Пользователь не вводит лицо в консоли.

`urblock_po` подключает распознавание лица к **PAM экрана GDM/блокировки**.  
Фоновый `urblock-verify` включает камеру и показывает **графический индикатор** (`overlay_ui.py`).

**Важно:** вход по лицу выполняется **только если** в Urblock GUI включена галочка  
«Включить автономную детекцию» (`auto_detect_enabled: true` в `urblock_gui/data/settings.json`).  
Галочка снята — остаётся обычный пароль на экране GDM.

## 1. Модели и зависимости

```bash
# ONNX в urblock_gui/models/
ls ../urblock_gui/models/*.onnx

# venv urblock_gui
cd ../urblock_gui && python3 -m venv .venv && .venv/bin/pip install opencv-python-headless numpy cryptography
```

## 2. Регистрация лица (из своей сессии, не на экране входа)

```bash
cd /home/ububu/urblock/urblock_po
./scripts/urblock-enroll --samples 3
```

Файлы: `../urblock_gui/data/users/<логин>/biometrics/*.vault`

## 3. Проверка для разработчика (не для конечного пользователя)

Имитация PAM из терминала — **не заменяет** экран GDM:

```bash
sudo PAM_USER="$(whoami)" ./scripts/urblock-verify --face-only
# код 0 = лицо совпало (камера + ONNX, без графического экрана входа)
```

## 4. Установка в PAM

```bash
sudo ./scripts/install-login.sh
```

На экране блокировки: камера включается в фоне. Если лицо распознано — сессия **разблокируется сама** (`loginctl unlock-session`), Enter не нужен.  
Если авторазблок не сработал — нажмите Enter (пароль не обязателен, если лицо уже узнано).

На экране входа GDM: выберите пользователя — камера в фоне, **справа сверху окно Urblock** (нужен `python3-tk`).  
Запасной статус, если окно не видно: `/run/user/<uid>/urblock-lock-status.txt`

- «Лицо не видно» — в кадре никого;
- «Лицо не распознано» — лицо есть, но не совпало с эталоном;
- «Лицо распознано» — можно нажать Enter (пароль не обязателен, если лицо уже подтверждено);
- параллельно можно **вводить пароль** и войти по паролю.

Скорость: кадр 480×360, детекция по ширине 320 px, ~25 проверок/с (CPU + ONNX).  
Первый вход после регистрации дольше, если нет файлов `.npy` — они строятся один раз.

PAM (`urblock-auth.conf`):

1. `urblock-verify --preflight` — камера в фоне (не блокирует);
2. `pam_unix` — **верный пароль → сразу вход**;
3. `urblock-verify` (optional) — только лицо; сбой не мешает паролю.

Переустановка: `sudo ./scripts/install-login.sh`

Окно рисуется через XWayland (`DISPLAY`, `XAUTHORITY` сессии пользователя).  
На чистом Wayland без XWayland — только файл статуса; распознавание в фоне продолжается  
(см. `/var/log/urblock-verify.log`).

| Ситуация | Компонент |
|----------|-----------|
| Win+L (блокировка) | `install-lock-agent.sh` (user systemd) |
| Разлогин → экран GDM | `sudo install-greeter-agent.sh` (system systemd) + PAM `install-login.sh` |

На экране входа после разлогина камера включается **до Enter**, как при Win+L.  
При `auto_unlock_on_face: true` после лица отправляется Enter в greeter → PAM завершает вход **без ручного Enter**.  
На GDM (Wayland) нужен **ydotool** + системный демон:  
`sudo apt install ydotool` и `sudo ./scripts/install-ydotoold-system.sh`  
(в Ubuntu нет `ydotoold.service` — только user-сервис `ydotool`, для GDM нужен root).  
`xdotool` один часто шлёт Enter не в ту форму — в логе «Enter ok», но вход не происходит.  
Если автовход не сработал — нажмите Enter или введите пароль.

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `URBLOCK_GUI_ROOT` | Путь к urblock_gui (модели, Python-модули) |
| `URBLOCK_DATA_DIR` | Каталог data с users/…/biometrics |
| `PAM_USER` | Логин при проверке (ставит PAM) |
| `URBLOCK_LOGIN_OVERLAY` | `0` — без графического индикатора |
| `URBLOCK_OVERLAY_MODE` | `tk` (по умолчанию) — окно на экране; `notify` — только уведомления |

## Откат

```bash
sudo ./scripts/disable-login.sh
```
