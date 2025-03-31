import asyncio
import os
import logging
import subprocess
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")

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
    orientation = "horizontal" if width > height else "vertical"

    input_file = f"input_{message.from_user.id}.mp4"
    output_file = f"output_{message.from_user.id}.mp4"

    file = await bot.get_file(file_id)
    if file.file_size > 49 * 1024 * 1024:
        await message.answer("Файл слишком большой (более 49 МБ). Пожалуйста, сократи его 💾")
        return

    await bot.download_file(file.file_path, input_file)
    await message.answer("🔄 Обрабатываю видео...")

    if orientation == "horizontal":
        crop_expr = "crop=in_h:in_h:(in_w-in_h)/2:0"
    else:
        crop_expr = "crop=in_w:in_w:0:(in_h-in_w)/2 - 60"  # немного выше центра

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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
