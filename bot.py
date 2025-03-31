import asyncio
import os
import logging
import subprocess
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "circlebotsecret")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

processed_messages = set()

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("Привет! Отправь мне видео, и я сделаю из него видеокружок 🎥")

@dp.message(F.video | F.video_note)
async def handle_video(message: types.Message):
    if message.message_id in processed_messages:
        return
    processed_messages.add(message.message_id)

    video = message.video or message.video_note
    file_id = video.file_id
    width = video.width
    height = video.height

    input_file = f"input_{message.from_user.id}.mp4"
    output_file = f"output_{message.from_user.id}.mp4"

    file = await bot.get_file(file_id)
    if file.file_size > 49 * 1024 * 1024:
        await message.answer("Файл слишком большой (более 49 МБ). Пожалуйста, сократи его или сожми 💾")
        return

    await message.answer("🔄 Обрабатываю видео, подожди немного...")
    await bot.download_file(file.file_path, input_file)

    if width > height:
        crop_expr = "crop=in_h:in_h:(in_w-in_h)/2:0"
    else:
        crop_expr = "crop=in_w:in_w:0:(in_h-in_w)/3"  # немного выше центра

    cmd = [
        "ffmpeg", "-y", "-i", input_file, "-t", "60",
        "-vf", f"{crop_expr},scale=480:480",
        "-c:v", "libx264", "-profile:v", "main", "-level", "3.1", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
    await message.reply_video_note(FSInputFile(output_file))

    for file in [input_file, output_file]:
        if os.path.exists(file):
            os.remove(file)

async def on_startup(_: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")

    # Запускаем пульс логгера
    asyncio.create_task(heartbeat())

async def on_shutdown(_: web.Application):
    await bot.delete_webhook()
    logging.info("Webhook удалён")

async def handle_webhook(request: web.Request):
    try:
        logging.info("📮 Пришёл запрос на вебхук!")
        data = await request.json()
        logging.info("🔥 RAW UPDATE:", data)
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception("Ошибка при обработке вебхука:")
    return web.Response()

async def heartbeat():
    while True:
        logging.info("💓 Бот живой, всё норм")
        await asyncio.sleep(300)  # раз в 5 минут

app = web.Application()
app.router.add_post(WEBHOOK_PATH, handle_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    try:
        logging.info("🔥 Стартуем aiohttp web-приложение")
        web.run_app(app, port=int(os.getenv("PORT", 10000)))
    except Exception as e:
        logging.exception(f"🚨 Ошибка при запуске web.run_app: {e}")
