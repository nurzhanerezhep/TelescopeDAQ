"""Build an illustrated Russian PDF without modifying the source screenshots."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

PROJECT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = landscape(A4)
INK = colors.HexColor("#1d3038")
TEAL = colors.HexColor("#096d78")
AMBER = colors.HexColor("#ffd166")
STYLE = ParagraphStyle(
    "Body", fontName="Manual", fontSize=10, leading=14, textColor=INK
)
SMALL = ParagraphStyle("Small", parent=STYLE, fontSize=9, leading=12)


def paragraph(c, text, x, top, width, style=STYLE):
    p = Paragraph(text, style)
    _, height = p.wrap(width, HEIGHT)
    p.drawOn(c, x, top - height)
    return top - height


def page(c, title, subtitle):
    c.setFillColor(TEAL)
    c.rect(0, HEIGHT - 9, WIDTH, 9, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("ManualBold", 22)
    c.drawString(32, HEIGHT - 48, title)
    paragraph(c, subtitle, 32, HEIGHT - 65, WIDTH - 64, SMALL)
    c.setStrokeColor(colors.HexColor("#cddadd"))
    c.line(32, 32, WIDTH - 32, 32)
    c.setFont("Manual", 8)
    c.setFillColor(TEAL)
    c.drawString(
        32, 18, "TelescopeDAQ | CAEN DT5740D | Руководство оператора | 23.09.2026"
    )
    c.drawRightString(WIDTH - 32, 18, str(c.getPageNumber()))


def note(c, heading, text, top):
    paragraph(c, f"<b>{heading}</b>", 32, top, WIDTH - 64)
    return paragraph(c, text, 32, top - 22, WIDTH - 64) - 18


def screenshot(c, filename, box, crop=None):
    image = ImageReader(str(PROJECT / "docs" / filename))
    iw, ih = image.getSize()
    x0, y0, x1, y1 = crop or (0, 0, iw, ih)
    bx, by, bw, bh = box
    scale = min(bw / (x1 - x0), bh / (y1 - y0))
    width, height = (x1 - x0) * scale, (y1 - y0) * scale
    y = by + bh - height
    c.saveState()
    clip = c.beginPath()
    clip.rect(bx, y, width, height)
    c.clipPath(clip, stroke=0)
    c.drawImage(
        image,
        bx - x0 * scale,
        y - (ih - y1) * scale,
        width=iw * scale,
        height=ih * scale,
        mask="auto",
    )
    c.restoreState()

    def point(raw):
        return bx + (raw[0] - x0) * scale, y + (y1 - raw[1]) * scale

    return point


def arrow(c, number, origin, target):
    x, y = origin
    tx, ty = target
    angle = math.atan2(ty - y, tx - x)
    c.setStrokeColor(AMBER)
    c.setFillColor(AMBER)
    c.setLineWidth(1.3)
    c.line(x + 9 * math.cos(angle), y + 9 * math.sin(angle), tx, ty)
    head = c.beginPath()
    head.moveTo(tx, ty)
    head.lineTo(tx - 6 * math.cos(angle - 0.4), ty - 6 * math.sin(angle - 0.4))
    head.lineTo(tx - 6 * math.cos(angle + 0.4), ty - 6 * math.sin(angle + 0.4))
    head.close()
    c.drawPath(head, fill=1, stroke=0)
    c.circle(x, y, 9, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("ManualBold", 10)
    c.drawCentredString(x, y - 3.4, str(number))


def figure_page(c, title, filename, items, description, crop=None, detail=None):
    page(
        c,
        title,
        "Номера на изображении соответствуют пояснениям справа. Стрелки указывают на элементы интерфейса.",
    )
    point = screenshot(c, filename, (32, 168, 554, 327), crop)
    top = 495
    for number, (label, body, origin, target) in enumerate(items, 1):
        arrow(c, number, point(origin), point(target))
        top = (
            paragraph(
                c, f"<b>{number}. {label}</b><br/>{body}", 607, top, WIDTH - 639, SMALL
            )
            - 13
        )
    if top < 145:
        raise ValueError(f"Legend too long on {title}")
    if detail:
        paragraph(c, "<b>Увеличенный фрагмент</b>", 32, 147, 210, SMALL)
        screenshot(c, filename, (32, 68, 360, 60), detail)
        paragraph(c, description, 413, 141, WIDTH - 445, SMALL)
    else:
        paragraph(c, description, 32, 145, WIDTH - 64)
    c.showPage()


def build(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=(WIDTH, HEIGHT))
    c.setTitle("TelescopeDAQ: иллюстрированное руководство пользователя")
    c.setAuthor("TelescopeDAQ")
    page(c, "TelescopeDAQ", "Иллюстрированное руководство пользователя веб-версии 0.3")
    top = note(
        c,
        "Что делает программа",
        "Читает waveform с CAEN DT5740D через USB и записывает исходные ADC samples в ROOT. Поддерживает Full Monitor, Write Only и независимый ROOT Viewer.",
        475,
    )
    top = note(
        c,
        "1. Запуск на компьютере регистрации",
        "Из корня проекта: <b>.\\.venv\\Scripts\\python.exe scripts/start_web.py</b><br/>Откройте адрес из терминала, обычно http://127.0.0.1:8000. До Connect или Start Run прибор не открывается. Для проверки без оборудования добавьте --demo.",
        top,
    )
    top = note(
        c,
        "2. Обычный рабочий цикл",
        "Settings: новый Run ID и параметры → Save YAML → выбор DAQ/Trigger Mode → Connect → Start Run → контроль статистики → Stop либо Safe Exit. Нельзя одновременно использовать CAEN из WaveDump, CLI, Tk GUI и веб-сервера.",
        top,
    )
    top = note(
        c,
        "Как читать это руководство",
        "Страницы 2-8: ваши шесть скриншотов с номерами и стрелками. Страницы 9-12: триггеры и scan, безопасный выход, сеть и диагностика. Скриншоты показывают disconnected и пустые графики: они не являются результатом аппаратного теста.",
        top,
    )
    note(
        c,
        "Важно",
        "Baseline, amplitude и charge не рассчитываются и не хранятся. External TRG-IN реализован программно, но требует стендовой проверки. Safe Exit не заменяет аппаратную защиту и не может прервать зависшую DLL.",
        top,
    )
    c.showPage()

    figure_page(
        c,
        "Run Control: запуск и состояние",
        "Run_control.png",
        [
            (
                "Режимы",
                "DAQ Mode: отображение. Trigger Mode: источник срабатывания.",
                (300, 65),
                (290, 133),
            ),
            (
                "Connect / Disconnect",
                "Подключение к CAEN и освобождение USB handle.",
                (725, 75),
                (533, 133),
            ),
            (
                "Start / Stop",
                "Начать run или закрыть запись, оставив сервер работающим.",
                (1390, 70),
                (1510, 133),
            ),
            (
                "Счётчики",
                "Accepted: triggers. Written: waveform отдельных каналов.",
                (715, 340),
                (739, 266),
            ),
            (
                "Instrument status",
                "Модель, timestamp и ошибки CAEN. Не путать с доступностью сервера.",
                (1455, 450),
                (1620, 438),
            ),
            (
                "Threshold Scan",
                "Отдельная проверка порога ch0; только вне run.",
                (1430, 580),
                (1630, 577),
            ),
            (
                "Channels Overview",
                "Каналы записи и последние экранные метаданные.",
                (885, 642),
                (990, 738),
            ),
        ],
        "<b>Порядок:</b> назначьте новый Run ID в Settings, сохраните YAML, выберите режимы, затем Start Run. При 16 каналах 100 физических событий обычно дают 1600 записанных waveform. Существующие ROOT и копия YAML с тем же Run ID не перезаписываются.",
    )

    figure_page(
        c,
        "Settings: запись и каналы",
        "Settings.png",
        [
            (
                "Run ID",
                "Уникальный номер нового файла run_NNNNNN.root.",
                (400, 143),
                (550, 177),
            ),
            (
                "Max events",
                "Лимит физических событий, не строк ROOT.",
                (380, 345),
                (550, 295),
            ),
            (
                "Record length",
                "Число отсчётов на канал. 1024 × 16 ns = 16.384 us.",
                (900, 380),
                (1080, 413),
            ),
            (
                "Pre-trigger / offset",
                "Положение окна и аппаратное групповое смещение.",
                (915, 540),
                (1085, 488),
            ),
            (
                "Record channels",
                "Включение ch0..15 в ROOT. Threshold требует ch0.",
                (1415, 397),
                (1637, 287),
            ),
            (
                "Save YAML",
                "Сохранить проверенные значения; Reload отменяет черновик.",
                (1475, 110),
                (1694, 55),
            ),
        ],
        "Изменение режима DAQ не подменяет параметры каналов. Ошибочные значения выделяются сразу; Save/Start блокируются. Во время run редактирование запрещено. Для смены USB link сначала нужен Disconnect. Offset и self-trigger аппаратно групповые, не независимые для каждого канала.",
        crop=(180, 0, 1770, 600),
    )

    figure_page(
        c,
        "Settings: триггеры и монитор",
        "Settings.png",
        [
            (
                "Threshold",
                "Абсолютный порог ch0: 0..4095 ADC, не амплитуда над baseline.",
                (375, 622),
                (560, 710),
            ),
            (
                "External",
                "TRG-IN, NIM/TTL, поддерживаемый ведущий фронт.",
                (915, 605),
                (1083, 707),
            ),
            (
                "Periodic",
                "Программный триггер каждые N секунд.",
                (1455, 604),
                (1630, 667),
            ),
            (
                "Compression",
                "none: меньше CPU; zlib: меньше объём на диске.",
                (395, 984),
                (548, 950),
            ),
            (
                "Display holdoff",
                "Пауза между кадрами экрана, не между записываемыми событиями.",
                (892, 914),
                (1085, 938),
            ),
            (
                "Event count window",
                "Длительность скользящего окна подсчёта событий.",
                (891, 1006),
                (1090, 977),
            ),
        ],
        "В Full Monitor для экрана берутся только последние кадры. Write Only сохраняет те же raw waveform без подготовки и рисования online-графиков. Periodic log управляет периодическими сообщениями, а не записью ROOT. Legacy prefix оставлен для совместимости: имена определяются Run ID.",
        crop=(180, 590, 1770, 1028),
    )

    figure_page(
        c,
        "Online Waveform: что видно на экране",
        "Online_waveform.png",
        [
            (
                "Каналы отображения",
                "Можно выбрать один или несколько. На состав ROOT не влияет.",
                (462, 357),
                (407, 258),
            ),
            (
                "Auto scale",
                "Автомасштаб Y; без него 0..4095 ADC.",
                (1430, 316),
                (1483, 211),
            ),
            ("Threshold", "Линия установленного порога ch0.", (1512, 400), (1570, 212)),
            (
                "Pause display",
                "Остановить только отображение. Acquisition продолжается.",
                (1603, 327),
                (1654, 212),
            ),
            ("Экспорт PNG", "Скачать текущий canvas.", (1707, 406), (1735, 212)),
            (
                "Display sampled",
                "Не все события рисуются. ROOT получает полные samples.",
                (700, 417),
                (322, 303),
            ),
        ],
        "Пустая область на исходном скриншоте соответствует состоянию disconnected. После запуска Full Monitor здесь появятся samples по X и ADC по Y. Для длинных waveform экранное прореживание сохраняет минимумы и максимумы блоков; ROOT не прореживается.",
        crop=(180, 180, 1775, 780),
    )

    figure_page(
        c,
        "Run Statistics: события за интервал",
        "Run_statistics.png",
        [
            (
                "Accepted events",
                "Число реально прочитанных физических событий в окне.",
                (480, 315),
                (342, 200),
            ),
            (
                "Sliding window",
                "Окно N секунд задаётся в Settings → Monitor.",
                (1470, 285),
                (1685, 200),
            ),
            (
                "Время по X",
                "Последние 30 минут; ноль соответствует текущему моменту.",
                (900, 495),
                (980, 608),
            ),
            (
                "Связь с CAEN",
                "Disconnected не означает остановленный HTTP-сервер.",
                (1450, 115),
                (1655, 43),
            ),
        ],
        "Это линейный график числа событий в скользящем окне, не гистограмма samples и не частота генератора. USB передаёт события пакетами, поэтому короткие окна неравномерны. Скорость в API рассчитывается как число событий / наблюдавшееся время. Наносекундная arrival-rate статистика здесь не обещается.",
        crop=(180, 0, 1775, 680),
    )

    figure_page(
        c,
        "ROOT Viewer: анализ закрытого файла",
        "ROOT_viewer.png",
        [
            (
                "Список файлов",
                "Выберите завершённый ROOT, затем Open.",
                (751, 372),
                (697, 274),
            ),
            (
                "Обновление / upload",
                "Обновить список или загрузить файл до 512 MiB.",
                (1558, 347),
                (1697, 223),
            ),
            (
                "Entry и стрелки",
                "Индекс waveform-строки с нуля, не физический event ID.",
                (282, 434),
                (333, 321),
            ),
            ("Show", "Показать waveform выбранной записи.", (503, 447), (449, 321)),
            (
                "Временной график",
                "Число физических событий по относительным timestamp ticks.",
                (782, 721),
                (485, 792),
            ),
        ],
        "Viewer не использует CAEN и может работать параллельно другому run. Текущий записываемый ROOT недоступен до закрытия. Большой анализ всё же использует CPU/диск. Для файлов свыше 512 MiB используйте папку output. На скриншоте файл ещё не выбран, поэтому графики пустые.",
        crop=(180, 175, 1775, 860),
    )

    figure_page(
        c,
        "Logs: сообщения и диагностика",
        "Logs.png",
        [
            (
                "Фильтр",
                "Все уровни, предупреждения с ошибками или только ошибки.",
                (1423, 310),
                (1640, 205),
            ),
            ("Экспорт", "Скачать текущие сообщения журнала.", (1690, 360), (1736, 205)),
            (
                "Строка журнала",
                "Время, уровень и текст. INFO о старте не подтверждает наличие событий.",
                (980, 414),
                (452, 257),
            ),
        ],
        "После ошибки прочитайте полный текст, проверьте диск/USB и состояние run. Файл logs/web.log ротируется; в браузере хранятся последние 1000 сообщений. Ошибка CAEN не включает DEMO автоматически.",
        crop=(185, 175, 1770, 716),
        detail=(215, 239, 780, 275),
    )

    page(c, "Триггеры и Threshold Scan", "Проверка параметров до длительного run")
    top = note(
        c,
        "Threshold",
        "Подавайте сигнал на ch0, выберите знак импульса, offset и абсолютный порог. Для сканирования остановите run и откройте Threshold Scan. Задайте Lower, Upper, Step, Time/point и Max events/point. Start Scan не создаёт ROOT.",
        475,
    )
    top = note(
        c,
        "Как читать scan",
        "X = threshold ADC; Y = число физических срабатываний. Верхняя граница включается. Точка заканчивается по времени либо лимиту событий. Если лимит достигнут раньше, время экспозиции различается: сравнивайте также rate_hz из CSV. Щелчок выбирает порог → Use in Settings → Save YAML.",
        top,
    )
    top = note(
        c,
        "External / TRG-IN",
        "Согласуйте электрические уровни генератора и входа по руководству CAEN. В Settings выберите NIM/TTL, rising как ведущий фронт и сохранение всех включённых каналов. Затем Trigger Mode: External. Приложение проверяет обратным чтением режим/уровень. Falling отвергается. Стендовая проверка ещё необходима.",
        top,
    )
    top = note(
        c,
        "Periodic",
        "Задайте Trigger interval, s, сохраните YAML и выберите Periodic. Это расписание software trigger, полезное для фоновых waveform, но не точный аппаратный таймер. Вычисление baseline не добавляется.",
        top,
    )
    note(
        c,
        "Рекомендуемый переход к длительной записи",
        "Короткий Full Monitor для проверки waveform → Stop → новый Run ID → Write Only → Start Run. Следите за свободным диском и журналом.",
        top,
    )
    c.showPage()

    page(
        c,
        "Безопасная остановка и выход",
        "Новая кнопка Safe Exit добавлена после исходных скриншотов",
    )
    top = note(
        c,
        "Stop и Safe Exit различаются",
        "Stop останавливает run и закрывает ROOT, но оставляет сервер работающим. Safe Exit в верхней панели дополнительно завершает приложение. Открытие окна подтверждения ничего не останавливает; Cancel возвращает к работе.",
        475,
    )
    top = note(
        c,
        "Подтверждение Stop & Exit",
        "После подтверждения блокируются новые команды; текущий цикл получает сигнал остановки. Уже принятые waveform дописываются, ROOT закрывается, CAEN останавливается и освобождается в его рабочем потоке. Сервер ждёт завершения очистки.",
        top,
    )
    top = note(
        c,
        "Успешное завершение",
        "Дождитесь DAQ safely closed / Files closed. CAEN released. Затем HTTP-сервер завершается и страницу можно закрыть. Закрывать саму вкладку вместо Safe Exit нельзя считать остановкой программы: регистрация может продолжаться.",
        top,
    )
    top = note(
        c,
        "Если появилась ошибка",
        "При ошибке flush или закрытия CAEN программа не показывает успешный выход. Сервер остаётся для просмотра Logs, запуск новых операций заблокирован. Проверьте причину. Не обещается восстановление повреждённого или частично записанного ROOT.",
        top,
    )
    note(
        c,
        "Границы защиты",
        "Зависшая DLL может задержать выход: принудительное убийство потока не используется. Emergency Stop не заменяет аппаратный interlock. Stop сохраняет принятые данные, но не гарантирует считывание всех оставшихся событий из памяти CAEN. Ctrl+C в терминале также запускает штатную очистку.",
        top,
    )
    c.showPage()

    page(
        c,
        "Просмотр из локальной сети",
        "Отдельный режим --lan; управление остаётся на компьютере DAQ",
    )
    top = note(
        c,
        "1. Запустите сетевой режим",
        "На компьютере CAEN: <b>.\\.venv\\Scripts\\python.exe scripts/start_web.py --lan</b><br/>Для обучения без прибора: добавьте --demo. Сначала штатно завершите прежний сервер; не запускайте два аппаратных сервера одновременно.",
        475,
    )
    top = note(
        c,
        "2. Откройте правильный адрес",
        "На компьютере регистрации используйте http://127.0.0.1:8000 для управления. На другом компьютере откройте LAN viewer URL из терминала. Например http://192.168.1.20:8000, где IP должен быть адресом именно DAQ-компьютера. 127.0.0.1 на каждом компьютере означает его самого.",
        top,
    )
    top = note(
        c,
        "3. Что доступно наблюдателю",
        "Статус, графики, Logs, просмотр параметров и закрытых ROOT. Нельзя Start/Stop, Safe Exit, scan, сохранять Settings или загружать файлы. Ограничения проверяются сервером по фактическому адресу клиента; отключены доверенные proxy headers.",
        top,
    )
    top = note(
        c,
        "4. Сеть и Firewall",
        "Компьютеры должны иметь сетевой доступ друг к другу. При необходимости разрешите Python/порт в Windows Firewall только для доверенного частного профиля и локальной подсети. Если порт занят, смотрите выбранный порт в логе. Брандмауэр программа сама не изменяет.",
        top,
    )
    note(
        c,
        "Ограничение безопасности",
        "Режим предназначен для доверенной приватной IPv4-сети. Пароль и TLS не реализованы: участники этой сети могут читать данные/логи. Не используйте в публичном Wi-Fi, через проброс портов или reverse proxy в Интернет. Без --lan сервер слушает только loopback.",
        top,
    )
    c.showPage()

    page(c, "Памятка оператора", "Что проверить перед запуском и при сбое")
    top = note(
        c,
        "Нет событий",
        "Проверьте USB, подключение ch0/TRG-IN, сохранённый trigger mode, порог, знак сигнала, offset и интервалы Periodic. 100 кГц на генераторе не гарантируют 100 тысяч сохранённых многоканальных событий в секунду.",
        475,
    )
    top = note(
        c,
        "Run ID уже занят",
        "Назначьте новый номер. Не удаляйте старый ROOT или YAML только для обхода защиты. Даже после ошибки запуска может остаться диагностическая копия конфигурации.",
        top,
    )
    top = note(
        c,
        "Settings не сохраняются",
        "Исправьте подсвеченное поле; дождитесь проверки. При изменении YAML другой вкладкой нажмите Reload и повторите правки. Во время acquisition/scan сохранение запрещено. Перед сменой USB link выполните Disconnect.",
        top,
    )
    top = note(
        c,
        "Счётчики отличаются",
        "Один физический event_id может соответствовать 16 waveform-строкам. Written обновляется после flush. Время аппаратного timestamp хранится в сырых ticks; не переносите 16 ns waveform sample автоматически на TriggerTimeTag.",
        top,
    )
    top = note(
        c,
        "ROOT не открывается",
        "Дождитесь закрытия run. Viewer ожидает дерево events с event_id, channel, timestamp, trigger_type, waveform. Пустой или повреждённый файл не является нормальным результатом. Сохраните файл и журнал для диагностики.",
        top,
    )
    note(
        c,
        "Источники руководства",
        "Исходный код и docs/WEB_USER_MANUAL.md; ваши Run_control.png, Settings.png, Online_waveform.png, Run_statistics.png, ROOT_viewer.png, Logs.png. Стрелки добавлены в PDF, оригиналы PNG не изменены. Результаты программных тестов не заменяют испытаний на CAEN.",
        top,
    )
    c.showPage()
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "output/pdf/TelescopeDAQ_User_Manual.pdf",
    )
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/arial.ttf"))
    parser.add_argument(
        "--bold-font", type=Path, default=Path("C:/Windows/Fonts/arialbd.ttf")
    )
    args = parser.parse_args()
    pdfmetrics.registerFont(TTFont("Manual", str(args.font)))
    pdfmetrics.registerFont(TTFont("ManualBold", str(args.bold_font)))
    pdfmetrics.registerFontFamily(
        "Manual",
        normal="Manual",
        bold="ManualBold",
        italic="Manual",
        boldItalic="ManualBold",
    )
    build(args.output)
    print(f"Manual: {args.output}")


if __name__ == "__main__":
    main()
