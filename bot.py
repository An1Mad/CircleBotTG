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

    input_file = None
    output_file = None

    try:
        video = message.video or message.video_note
        file_id = video.file_id
        processing_message = await message.reply("🔄 Обрабатываю видео, подожди немного...")

        try:
            file = await bot.get_file(file_id)
        except Exception as api_error:
            logging.error(f"Ошибка при получении файла: {api_error}")
            await message.reply(
                "Telegram не разрешает скачать это видео через API 😢\n\n"
                "💡 Возможные причины:\n"
                "• Слишком высокое качество (например, 1080p с высоким битрейтом)\n"
                "• Видео загружено как файл, а не как видео\n"
                "• Telegram Desktop иногда отправляет неподходящий mp4\n\n"
                "✅ Что можно сделать:\n"
                "1. Отправь видео с телефона\n"
                "2. Или перешли его себе в «Избранное», а потом сюда\n"
                "3. Или сожми видео онлайн здесь: [tools.rotato.app/compress](https://tools.rotato.app/compress) 💻",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if file.file_size > 49 * 1024 * 1024:
            await message.reply("Файл слишком большой (более 49 МБ). Пожалуйста, сократи его или сожми 💾")
            return

        input_file = f"input_{message.from_user.id}.mp4"
        output_file = f"output_{message.from_user.id}.mp4"

        await bot.download_file(file.file_path, input_file)

        cmd = [
            "ffmpeg", "-y", "-i", input_file, "-t", "60",
            "-vf", "crop='min(in_w, in_h)':'min(in_w, in_h)',scale=480:480",
            "-c:v", "libx264", "-profile:v", "main", "-level", "3.1", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            output_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
        await message.reply_video_note(FSInputFile(output_file))

    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
        await message.reply("Произошла ошибка при обработке видео 😔")

    finally:
        try:
            await processing_message.delete()
        except:
            pass
        for file in [input_file, output_file]:
            if file and os.path.exists(file):
                os.remove(file)


async def on_startup(_: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(_: web.Application):
    await bot.delete_webhook()
    logging.info("Webhook удалён")


async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception("Ошибка при обработке вебхука:")
    return web.Response()


app = web.Application()
app.router.add_post(WEBHOOK_PATH, handle_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 10000)))
