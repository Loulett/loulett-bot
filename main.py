from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


def load_token() -> str:
    return Path(".token").read_text().strip()


async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hello World")


def main() -> None:
    app = ApplicationBuilder().token(load_token()).build()
    app.add_handler(CommandHandler("hello", hello))
    app.run_polling()


if __name__ == "__main__":
    main()
