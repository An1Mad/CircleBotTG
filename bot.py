import asyncio
import os
import logging
import subprocess
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
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
pending_videos = {}  # message_id -> (file_id, orientation, user_id)
custom_crop_coords = {}  # user_id -> (file_id, input_file, width, height)


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
    orientation = "horizontal" if width > height else "vertical"

    pending_videos[str(message.message_id)] = (file_id, orientation, message.from_user.id)

    buttons = []
    if orientation == "horizontal":
        buttons = [
            [InlineKeyboardButton(text="◀️ Слева", callback_data=f"crop:left:{message.message_id}"),
             InlineKeyboardButton(text="🔲 Центр", callback_data=f"crop:center:{message.message_id}"),
             InlineKeyboardButton(text="▶️ Справа", callback_data=f"crop:right:{message.message_id}")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="🔼 Сверху", callback_data=f"crop:top:{message.message_id}"),
             InlineKeyboardButton(text="🔳 Центр", callback_data=f"crop:center:{message.message_id}"),
             InlineKeyboardButton(text="🔽 Снизу", callback_data=f"crop:bottom:{message.message_id}")]
        ]

    buttons.append([InlineKeyboardButton(text="🎯 Свой выбор (ввести x:y)", callback_data=f"crop:custom:{message.message_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.reply("Какую часть видео оставить?", reply_markup=markup)


@dp.callback_query()
async def debug_all_callbacks(callback: CallbackQuery):
    logging.info(f"[DEBUG] Callback data: {callback.data}")
    await callback.answer("👀 Обработка…")


@dp.callback_query(F.data.regexp(r"^crop:(left|center|right|top|bottom):\d+$"))
async def crop_callback(callback: CallbackQuery):
    try:
        logging.info(f"[CALLBACK] Получен колбэк: {callback.data}")

        parts = callback.data.split(":")
        if len(parts) != 3:
            logging.warning(f"[CALLBACK] Неверный формат: {callback.data}")
            await callback.message.answer("⚠️ Ошибка формата кнопки")
            return

        _, position, msg_id = parts
        logging.info(f"[CALLBACK] position={position}, msg_id={msg_id}")

        if msg_id not in pending_videos:
            logging.warning(f"[CALLBACK] msg_id {msg_id} не найден в pending_videos")
            await callback.message.answer("⚠️ Видео не найдено, начни сначала")
            return

        file_id, orientation, user_id = pending_videos[msg_id]
        input_file = f"input_{user_id}.mp4"
        output_file = f"output_{user_id}.mp4"

        file = await bot.get_file(file_id)
        if file.file_size > 49 * 1024 * 1024:
            await callback.message.answer("Файл слишком большой (более 49 МБ). Пожалуйста, сократи его или сожми 💾")
            return

        await bot.download_file(file.file_path, input_file)

        if orientation == "horizontal":
            crop_expr = {
                "left": "crop=in_h:in_h:0:0",
                "center": "crop=in_h:in_h:(in_w-in_h)/2:0",
                "right": "crop=in_h:in_h:(in_w-in_h):0"
            }[position]
        else:
            crop_expr = {
                "top": "crop=in_w:in_w:0:0",
                "center": "crop=in_w:in_w:0:(in_h-in_w)/2",
                "bottom": "crop=in_w:in_w:0:(in_h-in_w)"
            }[position]

        await callback.message.edit_text("🔄 Обрабатываю видео, подожди немного...")

        preview_file = f"preview_{user_id}.jpg"
        subprocess.run([
            "ffmpeg", "-i", input_file, "-ss", "00:00:01.000", "-vframes", "1",
            "-vf", crop_expr, preview_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        await callback.message.answer_photo(photo=FSInputFile(preview_file), caption="Вот как будет выглядеть кружок")

        cmd = [
            "ffmpeg", "-y", "-i", input_file, "-t", "60",
            "-vf", f"{crop_expr},scale=480:480",
            "-c:v", "libx264", "-profile:v", "main", "-level", "3.1", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            output_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
        await callback.message.reply_video_note(FSInputFile(output_file))

    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
        await callback.message.answer("Произошла ошибка при обработке видео 😔")
    finally:
        for file in [input_file, output_file, f"preview_{user_id}.jpg"]:
            if file and os.path.exists(file):
                os.remove(file)


@dp.message(F.text.regexp(r"^\d+:\d+$"))
async def handle_custom_crop_input(message: types.Message):
    user_id = message.from_user.id
    if user_id not in custom_crop_coords:
        return

    try:
        x, y = message.text.strip().split(":")
        x = int(x)
        y = int(y)
        file_id, input_file, width, height = custom_crop_coords.pop(user_id)
        output_file = f"output_{user_id}.mp4"

        if x < 0 or y < 0 or x + 480 > width or y + 480 > height:
            await message.reply("❌ Неверные координаты. Область crop должна помещаться в видео (480x480). Попробуйте снова.")
            return

        crop_expr = f"crop=480:480:{x}:{y}"
        preview_file = f"preview_{user_id}.jpg"

        subprocess.run([
            "ffmpeg", "-i", input_file, "-ss", "00:00:01.000", "-vframes", "1",
            "-vf", crop_expr, preview_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        await message.reply_photo(FSInputFile(preview_file), caption="Вот как будет выглядеть кружок")
        await message.reply("🔄 Обрабатываю видео по вашим координатам...")

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

    except Exception as e:
        logging.error(f"Ошибка при пользовательском crop: {e}")
        await message.answer("Произошла ошибка при пользовательской обрезке 😔")
    finally:
        for file in [input_file, output_file, f"preview_{user_id}.jpg"]:
            if file and os.path.exists(file):
                os.remove(file)


async def on_startup(_: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(_: web.Application):
    await bot.delete_webhook()
    logging.info("Webhook удалён")


async def handle_webhook(request: web.Request):
    print("💥 Пришёл запрос на вебхук!")
    ...
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
