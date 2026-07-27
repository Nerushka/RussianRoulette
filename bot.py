import os
import random
import threading
import time
import telebot
import time
from telebot import types
from supabase import create_client, Client
from http.server import HTTPServer, BaseHTTPRequestHandler

TOKEN = os.getenv('BOT_TOKEN')

# --- НАСТРОЙКИ SUPABASE ---
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TOKEN)

db_lock = threading.Lock()

def update_user_stat(user_id, chat_id, user_name, mode, result, streak_value=0):
    def background_task():
        with db_lock:
            try:
                response = supabase.table('user_stats').select('*').eq('user_id', user_id).eq('chat_id', chat_id).execute()
                rows = response.data
                
                if not rows:
                    supabase.table('user_stats').insert({
                        'user_id': user_id,
                        'chat_id': chat_id,
                        'user_name': user_name,
                        'classic_games': 0, 'classic_wins': 0, 'classic_deaths': 0,
                        'realistic_games': 0, 'realistic_wins': 0, 'realistic_deaths': 0,
                        'solo_games': 0, 'solo_survived': 0, 'solo_deaths': 0, 'solo_max_streak': 0
                    }).execute()
                    
                    response = supabase.table('user_stats').select('*').eq('user_id', user_id).eq('chat_id', chat_id).execute()
                    rows = response.data

                current_data = rows[0]
                update_payload = {'user_name': user_name}
                
                if mode == 'classic':
                    update_payload['classic_games'] = current_data['classic_games'] + 1
                    if result == 'win':
                        update_payload['classic_wins'] = current_data['classic_wins'] + 1
                    elif result == 'death':
                        update_payload['classic_deaths'] = current_data['classic_deaths'] + 1
                        
                elif mode == 'realistic':
                    update_payload['realistic_games'] = current_data['realistic_games'] + 1
                    if result == 'win':
                        update_payload['realistic_wins'] = current_data['realistic_wins'] + 1
                    elif result == 'death':
                        update_payload['realistic_deaths'] = current_data['realistic_deaths'] + 1
                        
                elif mode == 'solo':
                    update_payload['solo_games'] = current_data['solo_games'] + 1
                    if result == 'survived':
                        update_payload['solo_survived'] = current_data['solo_survived'] + 1
                    elif result == 'death':
                        update_payload['solo_deaths'] = current_data['solo_deaths'] + 1
                    
                    if streak_value > current_data['solo_max_streak']:
                        update_payload['solo_max_streak'] = streak_value

                supabase.table('user_stats').update(update_payload).eq('user_id', user_id).eq('chat_id', chat_id).execute()
                
            except Exception as e:
                print(f"Ошибка при обновлении базы данных Supabase: {e}")

    threading.Thread(target=background_task).start()

bot.set_my_commands([
    types.BotCommand('/start', 'Главное меню'),
    types.BotCommand('/help', 'Список всех команд'),
     types.BotCommand('/roulette', 'Классическая игра'),
    types.BotCommand('/roulette_realistic', 'Реализм'),
    types.BotCommand('/roulette_solo', 'Соло (серия побед)'),
    types.BotCommand('/mystats', 'Моя статистика'),
    types.BotCommand('/top', 'Топ лучших игроков'),
    types.BotCommand('/stopgame', 'Сбросить текущую игру'),
    types.BotCommand('/stopsolo', 'Сбросить активную соло игру'),
])

games = {}         
afk_timers = {}
solo_streaks = {}   
solo_owners = {}   
active_solo_users = {} 
user_cooldowns = {} 
COOLDOWN_TIME = 2.0 

def cancel_afk_timer(chat_id):
    if chat_id in afk_timers:
        afk_timers[chat_id].cancel()
        del afk_timers[chat_id]

def start_afk_timer(chat_id):
    cancel_afk_timer(chat_id)
    timer = threading.Timer(60.0, handle_afk, args=[chat_id])
    afk_timers[chat_id] = timer
    timer.start()

def handle_afk(chat_id):
    if chat_id not in games:
        return
    game = games[chat_id]
    if game.get('status') != 'playing':
        return
        
    current_player = game['players'][game['current_turn']]
    was_already_afk = current_player.get('is_afk', False)
    current_player['is_afk'] = True
    
    player_link = f'<a href="tg://user?id={current_player["id"]}">{current_player["name"]}</a>'
    markup = types.InlineKeyboardMarkup()
    callback_data_btn = 'shoot_afk_realistic' if game['mode'] == 'realistic' else 'shoot_afk'
    markup.add(types.InlineKeyboardButton('Выстрелить', callback_data=callback_data_btn))
    
    if was_already_afk:
        text = f'🍺 Игрок <b>{player_link}</b> <b>уже уходил в запой (AFK)</b> ранее! Можете снова пальнуть в него.'
    else:
        text = f'😴 Игрок <b>{player_link}</b> ушел в запой (AFK) и пропустил ход! Можете выстрелить в него.'
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')

def is_user_admin(chat_id, user_id):
    try:
        chat_member = bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except Exception:
        return False

@bot.message_handler(commands=['mystats'])
def show_my_stats(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Эту команду можно использовать только в групповых чатах!')
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    def background_stats():
        with db_lock:
            try:
                response = supabase.table('user_stats').select('*').eq('user_id', user_id).eq('chat_id', chat_id).execute()
                rows = response.data
                
                if not rows:
                    bot.reply_to(message, "📊 У тебя пока нет статистики в этом чате. Сыграй хотя бы одну игру!")
                    return
                
                row = rows[0]
                text = (
                    f"📊 <b>Статистика игрока {row['user_name']} в этом чате:</b>\n\n"
                    f"🎯 <b>Классический режим:</b>\n"
                    f"• Игр (<b>{row['classic_games']}</b>) | Побед (<b>{row['classic_wins']}</b>) | Смертей (<b>{row['classic_deaths']}</b>)\n\n"
                    f"💀 <b>Реалистичный режим:</b>\n"
                    f"• Игр (<b>{row['realistic_games']}</b>) | Побед (<b>{row['realistic_wins']}</b>) | Смертей (<b>{row['realistic_deaths']}</b>)\n\n"
                    f"🔥 <b>Одиночный режим:</b>\n"
                    f"• Рекордный стрик (<b>{row['solo_max_streak']}</b>)"
                )
                bot.reply_to(message, text, parse_mode='HTML')
            except Exception as e:
                bot.reply_to(message, "⚠️ Ошибка подключения к базе данных.")

    threading.Thread(target=background_stats).start()

@bot.message_handler(commands=['top'])
def show_top_menu(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Эту команду можно использовать только в групповых чатах!')
        return
    chat_id = message.chat.id
    text, markup = generate_top_content(chat_id, 'classic')
    bot.reply_to(message, text, reply_markup=markup, parse_mode='HTML')

def generate_top_content(chat_id, mode):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton('🎯 Классика', callback_data='top_classic'),
        types.InlineKeyboardButton('💀 Реализм', callback_data='top_realistic'),
        types.InlineKeyboardButton('🔥 Соло', callback_data='top_solo')
    )
    
    with db_lock:
        try:
            if mode == 'classic':
                response = supabase.table('user_stats') \
                    .select('user_name, classic_wins, classic_games, classic_deaths') \
                    .eq('chat_id', chat_id) \
                    .gt('classic_games', 0) \
                    .order('classic_wins', desc=True) \
                    .order('classic_games', desc=False) \
                    .limit(10).execute()
                rows = response.data
                
                text = "🏆 <b>Топ игроков чата: Классический режим</b>\n\n"
                if not rows:
                    text += "<i>Пока нет данных по этому режиму.</i>"
                else:
                    for i, row in enumerate(rows, 1):
                        text += f"{i}. <b>{row['user_name']}</b> — Побед (<b>{row['classic_wins']}</b>) | Игр (<b>{row['classic_games']}</b>) | Смертей (<b>{row['classic_deaths']}</b>)\n"
                        
            elif mode == 'realistic':
                response = supabase.table('user_stats') \
                    .select('user_name, realistic_wins, realistic_games, realistic_deaths') \
                    .eq('chat_id', chat_id) \
                    .gt('realistic_games', 0) \
                    .order('realistic_wins', desc=True) \
                    .order('realistic_games', desc=False) \
                    .limit(10).execute()
                rows = response.data
                
                text = "🏆 <b>Топ игроков чата: Реалистичный режим</b>\n\n"
                if not rows:
                    text += "<i>Пока нет данных по этому режиму.</i>"
                else:
                    for i, row in enumerate(rows, 1):
                        text += f"{i}. <b>{row['user_name']}</b> — Побед (<b>{row['realistic_wins']}</b>) | Игр (<b>{row['realistic_games']}</b>) | Смертей (<b>{row['realistic_deaths']}</b>)\n"
                        
            elif mode == 'solo':
                response = supabase.table('user_stats') \
                    .select('user_name, solo_max_streak') \
                    .eq('chat_id', chat_id) \
                    .gt('solo_games', 0) \
                    .order('solo_max_streak', desc=True) \
                    .limit(10).execute()
                rows = response.data
                
                text = "🏆 <b>Топ игроков чата: Одиночный режим</b>\n\n"
                if not rows:
                    text += "<i>Пока нет данных по этому режиму.</i>"
                else:
                    for i, row in enumerate(rows, 1):
                        text += f"{i}. <b>{row['user_name']}</b> — Рекордный стрик (<b>{row['solo_max_streak']}</b>)\n"
        except Exception as e:
            text = "⚠️ Ошибка при загрузке топа из базы данных."
            
    return text, markup

@bot.message_handler(commands=['start'])
def send_start(message):
    if message.chat.type == 'private':
        bot_info = bot.get_me()
        bot_username = bot_info.username
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton('Добавить бота в чат', url=f'https://t.me/{bot_username}?startgroup=true'))
        
        text = (
            "Привет! Чтобы начать игру, добавь бота в свой чат.\n\n"
            "📖 Список всех доступных команд: /help"
        )
        bot.reply_to(message, text, reply_markup=markup, parse_mode='HTML')
    else:
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton('🎯 Классическая игра', callback_data='btn_roulette'),
            types.InlineKeyboardButton('💀 Реалистичный режим', callback_data='btn_realistic'),
            types.InlineKeyboardButton('🔥 Одиночный режим', callback_data='btn_solo')
        )
        
        text = (
            "Привет! Чтобы начать игру, выберите один из режимов:\n\n"
            "📖 Список всех доступных команд: /help"
        )
        bot.reply_to(message, text, reply_markup=markup, parse_mode='HTML')

@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = (
        "📖 <b>Все доступные команды:</b>\n\n"
        "• /start — Главное меню\n"
        "• /help — Список всех команд\n"
        "• /roulette — Классическая игра\n"
        "• /roulette_realistic — Реалистичный режим\n"
        "• /roulette_solo — Одиночный режим\n"
        "• /mystats — Моя статистика\n"
        "• /top — Топ лучших игроков\n"
        "• /stopgame — Сбросить текущую игру (только для админов)\n"
        "• /stopsolo — Сбросить активную соло игру\n\n"
        "⚙️ <b>Правила и особенности:</b>\n\n"
        "• <b>Классика:</b> При каждом ходе шанс схватить пулю всегда составляет 1 из 6 (барабан крутится заново).\n\n"
        "• <b>Реализм:</b> Один барабан на всю игру (с каждым выжившим риск возрастает).\n\n"
        "• <b>Соло:</b> Игра на рекордную серию побед.\n\n"
        "• <b>AFK-система:</b> Если игрок не делает ход в течение минуты, он уходит в запой, и любой желающий может выстрелить в него!"
    )
    bot.reply_to(message, help_text, parse_mode='HTML')

@bot.message_handler(commands=['stopgame'])
def stop_game(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Эту команду можно использовать только в групповых чатах!')
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_user_admin(chat_id, user_id):
        bot.reply_to(message, '❌ Команда /stopgame доступна только администраторам чата!')
        return

    cancel_afk_timer(chat_id)
    if chat_id in games:
        game = games[chat_id]
        msg_id = game.get('msg_id')
        if msg_id:
            try:
                stopped_text = get_message_content(game['mode'], game['players'], state='stopped')
                bot.edit_message_text(stopped_text, chat_id, msg_id, reply_markup=None, parse_mode='HTML')
            except Exception:
                pass
        del games[chat_id]
        bot.reply_to(message, '🛑 Текущая групповая игра сброшена администратором.')
    else:
        bot.reply_to(message, 'В этом чате нет активных групповых игр.')

@bot.message_handler(commands=['stopsolo'])
def stop_solo_game(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Эту команду можно использовать только в групповых чатах!')
        return
    user_id = message.from_user.id
    if user_id in active_solo_users:
        old_msg_id = active_solo_users[user_id]
        if old_msg_id in solo_owners:
            try:
                try:
                    msg = bot.get_message(message.chat.id, old_msg_id)
                    current_text = msg.text or ""
                except Exception:
                    current_text = "🎯 <b>Одиночный режим</b>"
                
                stopped_text = current_text + "\n\n❌ <b>Игра сброшена</b>"
                
                bot.edit_message_text(stopped_text, message.chat.id, old_msg_id, reply_markup=None, parse_mode='HTML')
            except Exception:
                pass
            del solo_owners[old_msg_id]
        del active_solo_users[user_id]
        if user_id in solo_streaks:
            del solo_streaks[user_id]
        bot.reply_to(message, '🛑 Ваша активная соло-игра сброшена.')
    else:
        bot.reply_to(message, 'У вас нет активных соло-игр.')

def get_message_content(mode, players, state='registration'):
    if mode == 'classic':
        if state == 'registration':
            title = "🎯 <b>Классический режим: Регистрация ✍️</b>"
        elif state == 'playing':
            title = "🎯 <b>Классический режим: Игра началась ✅</b>"
        elif state == 'finished':
            title = "🎯 <b>Классический режим: Игра закончена ✅</b>"
        elif state == 'stopped':
            title = "🎯 <b>Классический режим: Игра сброшена ❌</b>"
        else:
            title = "🎯 <b>Классический режим</b>"
        rule = "При каждом ходе шанс схватить пулю всегда составляет 1 из 6 (барабан крутится заново)."
    else:
        if state == 'registration':
            title = "💀 <b>Реалистичный режим: Регистрация ✍️</b>"
        elif state == 'playing':
            title = "💀 <b>Реалистичный режим: Игра началась ✅</b>"
        elif state == 'finished':
            title = "💀 <b>Реалистичный режим: Игра закончена ✅</b>"
        elif state == 'stopped':
            title = "💀 <b>Реалистичный режим: Игра сброшена ❌</b>"
        else:
            title = "💀 <b>Реалистичный режим</b>"
        rule = "Один барабан на всю игру (с каждым выжившим риск возрастает)."

    if state == 'stopped':
        middle_block = ""
    elif players:
        players_list_str = '\n'.join([f"• {p['name']}" for p in players])
        middle_block = f"\n<b>Участники:</b>\n{players_list_str}\n"
    else:
        middle_block = "\n<i>Пока никто не присоединился...</i>\n"

    rules_block = f"⚙️ <code>{rule}</code>"
    if middle_block:
        return f"{title}\n{middle_block}\n{rules_block}"
    else:
        return f"{title}\n\n{rules_block}"

@bot.message_handler(commands=['roulette'])
def create_game(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Групповую игру нужно запускать в чате!')
        return
    chat_id = message.chat.id
    if chat_id in games:
        bot.reply_to(message, "⚠️ В этом чате уже идет активная игра! Сначала завершите её или сбросьте через /stopgame.")
        return
    cancel_afk_timer(chat_id)
    if chat_id in games and games[chat_id]['status'] != 'registration':
        bot.reply_to(message, 'В этом чате уже идет игра!')
        return
    
    games[chat_id] = {'mode': 'classic', 'status': 'registration', 'players': [], 'current_turn': 0, 'msg_id': None, 'total_shots': 0}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Присоединиться', callback_data='join_game'))
    markup.add(types.InlineKeyboardButton('Начать игру', callback_data='start_game_btn'))
    
    text = get_message_content('classic', [], state='registration')
    sent_msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
    games[chat_id]['msg_id'] = sent_msg.message_id

@bot.message_handler(commands=['roulette_solo'])
def start_solo_game(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Одиночный режим доступен только в групповых чатах!')
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    if user_id in active_solo_users:
        bot.reply_to(message, '⚠️ У тебя уже начата игра в одиночном режиме! Заверши её или сбрось командой /stopsolo.')
        return
    
    solo_streaks[user_id] = 0
    
    text = f"🎯 <b>Одиночный режим️</b> | <a href='tg://user?id={user_id}'>{user_name}</a>\n\n🔫 Барабан заряжен. Сделай первый ход!"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Нажать на курок', callback_data='shoot_solo'))
    
    sent_msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
    solo_owners[sent_msg.message_id] = user_id
    active_solo_users[user_id] = sent_msg.message_id

@bot.message_handler(commands=['roulette_realistic'])
def create_realistic_game(message):
    if message.chat.type == 'private':
        bot.reply_to(message, '⚠️ Групповую игру нужно запускать в чате!')
        return
    chat_id = message.chat.id
    if chat_id in games:
        bot.reply_to(message, "⚠️ В этом чате уже идет активная игра! Сначала завершите её или сбросьте через /stopgame.")
        return
    cancel_afk_timer(chat_id)
    if chat_id in games and games[chat_id]['status'] != 'registration':
        bot.reply_to(message, 'В этом чате уже идет игра!')
        return
    
    games[chat_id] = {'mode': 'realistic', 'status': 'registration', 'players': [], 'current_turn': 0, 'msg_id': None, 'total_shots': 0}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Присоединиться', callback_data='join_game'))
    markup.add(types.InlineKeyboardButton('Начать игру', callback_data='start_game_btn'))
    
    text = get_message_content('realistic', [], state='registration')
    sent_msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
    games[chat_id]['msg_id'] = sent_msg.message_id

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user_name = call.from_user.first_name

    if call.data in ['top_classic', 'top_realistic', 'top_solo']:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        mode_map = {'top_classic': 'classic', 'top_realistic': 'realistic', 'top_solo': 'solo'}
        new_mode = mode_map[call.data]
        text, markup = generate_top_content(chat_id, new_mode)
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass
        return

    if call.data == 'btn_roulette':
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        create_game(call.message)
        return
    elif call.data == 'btn_realistic':
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        create_realistic_game(call.message)
        return
    elif call.data == 'btn_solo':
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        if user_id in active_solo_users:
            bot.send_message(chat_id, '⚠️ У тебя уже начата игра в одиночном режиме! Заверши её или сбрось командой /stopsolo.')
            return
        call.message.from_user = call.from_user
        start_solo_game(call.message)
        return

    if call.data in ['shoot_solo', 'restart_solo']:
        msg_id = call.message.message_id
        owner_id = solo_owners.get(msg_id)
        
        if owner_id and user_id != owner_id:
            try:
                bot.answer_callback_query(call.id, "❌ Это не твоя игра!", show_alert=True)
            except Exception:
                pass
            return

        current_time = time.time()
        if user_id in user_cooldowns:
            elapsed = current_time - user_cooldowns[user_id]
            if elapsed < COOLDOWN_TIME:
                try:
                    bot.answer_callback_query(call.id, f"⏳ Подожди еще {round(COOLDOWN_TIME - elapsed, 1)} сек.", show_alert=False)
                except Exception:
                    pass
                return
        user_cooldowns[user_id] = current_time

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        if call.data == 'restart_solo':
            solo_streaks[user_id] = 0
            text = f"🎯 <b>Одиночный режим️</b> | <a href='tg://user?id={user_id}'>{user_name}</a>\n\n🔫 Барабан заряжен. Сделай первый ход!"
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton('Нажать на курок', callback_data='shoot_solo'))
            
            try:
                sent_msg = bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode='HTML')
                if hasattr(sent_msg, 'message_id'):
                    if msg_id in solo_owners:
                        del solo_owners[msg_id]
                    solo_owners[sent_msg.message_id] = user_id
                    active_solo_users[user_id] = sent_msg.message_id
            except Exception:
                pass
            return

        if user_id not in solo_streaks:
            solo_streaks[user_id] = 0

        bullet = random.randint(1, 6)
        
        if bullet == 1:
            dead_streak = solo_streaks[user_id]
            update_user_stat(user_id, chat_id, user_name, 'solo', 'death', dead_streak)
            solo_streaks[user_id] = 0
            if user_id in active_solo_users:
                del active_solo_users[user_id]
            if msg_id in solo_owners:
                del solo_owners[msg_id]
            
            text = f"🎯 <b>Одиночный режим</b> | <a href='tg://user?id={user_id}'>{user_name}</a>\n\n💥 Поймал пулю! Стрик сгорел (<b>{dead_streak}</b>)."
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton('🔄 Начать заново', callback_data='restart_solo'))
            
            try:
                sent_msg = bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode='HTML')
                if hasattr(sent_msg, 'message_id'):
                    solo_owners[sent_msg.message_id] = user_id
                    active_solo_users[user_id] = sent_msg.message_id
            except Exception:
                pass
        else:
            solo_streaks[user_id] += 1
            curr = solo_streaks[user_id]
            update_user_stat(user_id, chat_id, user_name, 'solo', 'survived', curr)
            
            text = f"🎯 <b>Одиночный режим️</b> | <a href='tg://user?id={user_id}'>{user_name}</a>\n\n💨 Осечка! Выжил. 🔥 Стрик: <b>{curr}</b>"
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton('Нажать на курок', callback_data='shoot_solo'))
            
            try:
                sent_msg = bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode='HTML')
                if hasattr(sent_msg, 'message_id'):
                    if msg_id in solo_owners and msg_id != sent_msg.message_id:
                        del solo_owners[msg_id]
                    solo_owners[sent_msg.message_id] = user_id
                    active_solo_users[user_id] = sent_msg.message_id
            except Exception:
                pass
        return

    if chat_id not in games:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return
        
    game = games[chat_id]
    
    if call.data == 'join_game':
        if game['status'] != 'registration':
            return
        if any(p['id'] == user_id for p in game['players']):
            bot.answer_callback_query(call.id, '⚠️ Ты уже присоединился к игре!', show_alert=True)
            return
            
        game['players'].append({'id': user_id, 'name': user_name, 'status': 'alive', 'is_afk': False})
        try:
            bot.answer_callback_query(call.id, '✅ Ты успешно присоединился!')
        except Exception:
            pass
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton('Присоединиться', callback_data='join_game'))
        markup.add(types.InlineKeyboardButton('Начать игру', callback_data='start_game_btn'))
        
        text = get_message_content(game['mode'], game['players'], state='registration')
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass

    elif call.data == 'start_game_btn':
        if game['status'] != 'registration':
            return
        if len(game['players']) < 2:
            bot.answer_callback_query(call.id, '❌ Нельзя начать игру, пока ты один! Нужно минимум 2 игрока.', show_alert=True)
            return
            
        game['status'] = 'playing'
        game['total_shots'] = 0
        random.shuffle(game['players'])
        game['current_turn'] = 0
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        
        text = get_message_content(game['mode'], game['players'], state='playing')
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=None, parse_mode='HTML')
        except Exception:
            pass

        if game['mode'] == 'classic':
            send_next_turn(chat_id)
        elif game['mode'] == 'realistic':
            game['bullet_chamber'] = random.randint(1, 6)
            game['current_chamber'] = 1
            send_realistic_turn(chat_id)

    elif call.data in ['shoot_classic', 'shoot_afk']:
        if game['status'] != 'playing':
            return
        current_player = game['players'][game['current_turn']]
        is_afk_shot = (call.data == 'shoot_afk')
        
        if is_afk_shot and user_id == current_player['id']:
            return
        if not is_afk_shot and user_id != current_player['id']:
            bot.answer_callback_query(call.id, '❌ Не твоя очередь!', show_alert=True)
            return
            
        current_time = time.time()
        if user_id in user_cooldowns:
            elapsed = current_time - user_cooldowns[user_id]
            if elapsed < COOLDOWN_TIME:
                try:
                    bot.answer_callback_query(call.id, f"⏳ Подожди еще {round(COOLDOWN_TIME - elapsed, 1)} сек.", show_alert=False)
                except Exception:
                    pass
                return
        user_cooldowns[user_id] = current_time

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        cancel_afk_timer(chat_id)
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

        game['total_shots'] += 1
        bullet = random.randint(1, 6)
        if bullet == 1:
            current_player['status'] = 'dead'
            game['status'] = 'finished'
            
            for p in game['players']:
                if p['id'] == current_player['id']:
                    update_user_stat(p['id'], chat_id, p['name'], 'classic', 'death')
                else:
                    update_user_stat(p['id'], chat_id, p['name'], 'classic', 'win')
                    
            bot.send_message(chat_id, f"💥 <b>{current_player['name']}</b> поймал пулю и проиграл!", parse_mode='HTML')
            
            survivors_lines = [f"{p['name']} <b>Выжил</b> ✅" for p in game['players'] if p['id'] != current_player['id']]
            survivors_str = "\n".join(survivors_lines) if survivors_lines else "Нет"
            
            results_text = (
                f"🎯 <b>Классический режим: Результаты</b>\n\n"
                f"{survivors_str}\n\n"
                f"{current_player['name']} <b>Словил пулю</b> 🔥\n\n"
                f"<b>Игра закончилась на {game['total_shots']} выстреле!</b>"
            )
            bot.send_message(chat_id, results_text, parse_mode='HTML')
            
            msg_id = game.get('msg_id')
            if msg_id:
                try:
                    finished_text = get_message_content(game['mode'], game['players'], state='finished')
                    bot.edit_message_text(finished_text, chat_id, msg_id, reply_markup=None, parse_mode='HTML')
                except Exception:
                    pass
            
            del games[chat_id]
        else:
            bot.send_message(chat_id, f"💨 Осечка у <b>{current_player['name']}</b>.", parse_mode='HTML')
            game['current_turn'] += 1
            if game['current_turn'] >= len(game['players']):
                game['current_turn'] = 0
            send_next_turn(chat_id)

    elif call.data in ['shoot_realistic', 'shoot_afk_realistic']:
        if game['status'] != 'playing':
            return
        current_player = game['players'][game['current_turn']]
        is_afk_shot = (call.data == 'shoot_afk_realistic')
        
        if is_afk_shot and user_id == current_player['id']:
            return
        if not is_afk_shot and user_id != current_player['id']:
            bot.answer_callback_query(call.id, '❌ Не твоя очередь!', show_alert=True)
            return
            
        current_time = time.time()
        if user_id in user_cooldowns:
            elapsed = current_time - user_cooldowns[user_id]
            if elapsed < COOLDOWN_TIME:
                try:
                    bot.answer_callback_query(call.id, f"⏳ Подожди еще {round(COOLDOWN_TIME - elapsed, 1)} сек.", show_alert=False)
                except Exception:
                    pass
                return
        user_cooldowns[user_id] = current_time

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        cancel_afk_timer(chat_id)
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

        game['total_shots'] += 1

        if game['current_chamber'] >= 6:
            is_dead = True
        else:
            is_dead = (game['current_chamber'] == game['bullet_chamber'])

        if is_dead:
            game['status'] = 'finished'
            for p in game['players']:
                if p['id'] == current_player['id']:
                    update_user_stat(p['id'], chat_id, p['name'], 'realistic', 'death')
                else:
                    update_user_stat(p['id'], chat_id, p['name'], 'realistic', 'win')
                    
            bot.send_message(chat_id, f"💥 <b>{current_player['name']}</b> поймал пулю и проиграл!", parse_mode='HTML')
            
            survivors_lines = [f"{p['name']} <b>Выжил</b> ✅" for p in game['players'] if p['id'] != current_player['id']]
            survivors_str = "\n".join(survivors_lines) if survivors_lines else "Нет"
            
            results_text = (
                f"💀 <b>Реалистичный режим: Результаты</b>\n\n"
                f"{survivors_str}\n\n"
                f"{current_player['name']} <b>Словил пулю</b> 🔥\n\n"
                f"<b>Игра закончилась на {game['total_shots']} выстреле!</b>"
            )
            bot.send_message(chat_id, results_text, parse_mode='HTML')
            
            msg_id = game.get('msg_id')
            if msg_id:
                try:
                    finished_text = get_message_content(game['mode'], game['players'], state='finished')
                    bot.edit_message_text(finished_text, chat_id, msg_id, reply_markup=None, parse_mode='HTML')
                except Exception:
                    pass

            del games[chat_id]
        else:
            game['current_chamber'] += 1
            bot.send_message(chat_id, f"💨 Щелчок! Пусто. Камора {game['current_chamber']-1}/6 пройдена.", parse_mode='HTML')
            
            game['current_turn'] += 1
            if game['current_turn'] >= len(game['players']):
                game['current_turn'] = 0
            send_realistic_turn(chat_id)

def send_next_turn(chat_id):
    game = games[chat_id]
    current_player = game['players'][game['current_turn']]
    if current_player.get('is_afk', False):
        handle_afk(chat_id)
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Нажать на курок', callback_data='shoot_classic'))
    bot.send_message(chat_id, f'👉 Ход игрока <a href="tg://user?id={current_player["id"]}">{current_player["name"]}</a>', reply_markup=markup, parse_mode='HTML')
    start_afk_timer(chat_id)

def send_realistic_turn(chat_id):
    game = games[chat_id]
    current_player = game['players'][game['current_turn']]
    if current_player.get('is_afk', False):
        handle_afk(chat_id)
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Нажать на курок', callback_data='shoot_realistic'))
    bot.send_message(chat_id, f'👉 Ход игрока <a href="tg://user?id={current_player["id"]}">{current_player["name"]}</a> (Камора {game["current_chamber"]}/6)', reply_markup=markup, parse_mode='HTML')
    start_afk_timer(chat_id)

def keep_db_alive():
    while True:
        # Ждем 6 дней (6 дней * 24 часа * 60 минут * 60 секунд)
        time.sleep(6 * 24 * 60 * 60) 
        try:
            # Тот самый легкий запрос к таблице, который сбрасывает таймер сна базы
            supabase.table('user_stats').select('user_id').limit(1).execute()
        except Exception as e:
            print(f"Ошибка при пинге базы: {e}")

# Запускаем фоновый поток при старте бота
threading.Thread(target=keep_db_alive, daemon=True).start()

class SimpleHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"Bot is alive!")


def run_server():
  port = int(os.environ.get("PORT", 10000))
  server = HTTPServer(("0.0.0.0", port), SimpleHandler)
  server.serve_forever()


threading.Thread(target=run_server, daemon=True).start()

print("Бот с Supabase запущен...")
bot.infinity_polling()
