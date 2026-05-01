from aiogram import Router
from aiogram.types import ChatMemberUpdated
from bot.keyboards import main_menu_kb

router = Router()

@router.my_chat_member()
async def on_added(evt: ChatMemberUpdated):
    if evt.chat.type not in ("group", "supergroup"):
        return
    new = evt.new_chat_member
    if new and new.status in ("member", "administrator"):
        await evt.bot.send_message(evt.chat.id, "BazaTon main menu", reply_markup=main_menu_kb())
