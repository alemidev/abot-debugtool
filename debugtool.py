import asyncio
import io
import logging
import sys
import inspect

from alemibot import alemiBot

from pyrogram import filters
from pyrogram.enums import ParseMode, MessageEntityType
from pyrogram.types import MessageEntity, ReplyKeyboardMarkup

from alemibot.util.help import CATEGORIES
from alemibot.util.command import _Message as Message
from alemibot.util import (
	filterCommand, cleartermcolor, get_user, ProgressChatAction, is_me, edit_or_reply,
	sudo, is_allowed, report_error, set_offline, cancel_chat_action, HelpCategory
)

logger = logging.getLogger(__name__)

class stdoutWrapper(): 
	def __init__(self):
		self.buffer = io.StringIO()
		self.old_stdout = sys.stdout
		self.old_stderr = sys.stderr
		
	def __enter__(self):
		sys.stdout = self.buffer
		sys.stderr = self.buffer
		return self.buffer
	
	def __exit__(self, exc_type, exc_value, exc_traceback): 
		sys.stdout = self.old_stdout
		sys.stderr = self.old_stderr

HELP = HelpCategory("DEBUGTOOL")

@HELP.add(cmd="[<path>]")
@alemiBot.on_message(sudo & filterCommand("put"))
@report_error(logger)
@set_offline
@cancel_chat_action
async def put_cmd(client:alemiBot, message:Message):
	"""save file to server

	Reply to a media message or attach media to store file on server.
	File will be saved in `downloads` folder if no path is specified.
	"""
	msg = message
	prog = ProgressChatAction(client, message.chat.id, "find_location")
	dest_path = message.command[0] or "downloads/"
	if message.reply_to_message is not None:
		msg = message.reply_to_message
	if msg.media:
		fpath = await client.download_media(msg, file_name=dest_path, progress=prog.tick)
		await edit_or_reply(message, f'` → ` saved file as `{fpath}`')
	else:
		await edit_or_reply(message, "`[!] → ` No file given")

@HELP.add(cmd="<path>")
@alemiBot.on_message(sudo & filterCommand("get", flags=["-log"]))
@report_error(logger)
@set_offline
@cancel_chat_action
async def get_cmd(client:alemiBot, message:Message):
	"""request a file from server

	Will upload a file from server to this chat.
	The path can be absolute or relative (starting from alemiBot workdir).
	Use flag `-log` to automatically upload `log/<name>.log`.
	"""
	reply_to = message.reply_to_message
	if reply_to and reply_to.reply_markup and isinstance(reply_to.reply_markup, ReplyKeyboardMarkup):
		return await edit_or_reply(message, "`[!] → ` Not allowed from ReplyKeyboard")
	if len(message.command) < 1 and not message.command["-log"]:
		return await edit_or_reply(message, "`[!] → ` No input")
	prog = ProgressChatAction(client, message.chat.id)
	if message.command["-log"]: # ugly special case for debug.log
		with open(f"log/{client.name}.log") as f:
			logfile = f.read()
		if len(client.session_name) > 25: # It's most likely a session string
			logfile = logfile.replace(client.session_name, "client") # botchy fix for those using a session string
		log_io = io.BytesIO(logfile.encode("utf-8"))
		log_io.name = f"{client.name}.log"
		await client.send_document(message.chat.id, log_io, reply_to_message_id=message.id,
				caption='` → ` **logfile**', progress=prog.tick)
	else:
		await client.send_document(message.chat.id, message.command[0], reply_to_message_id=message.id,
				caption=f'` → ` **{message.command[0]}**', progress=prog.tick)

@HELP.add(cmd="<cmd>")
@alemiBot.on_message(sudo & filterCommand(["run", "r"], options={
	"timeout" : ["-t", "-time"]
}))
@set_offline
@cancel_chat_action
async def run_cmd(client:alemiBot, message:Message):
	"""run a command on server

	Shell will be from user running bot: every command starts in bot root folder.
	There is a timeout of 60 seconds to any command issued, this can be changed with the `-t` option.
	You should properly wrap your arguments with `\"`, they will be ignored by cmd parser.
	"""
	reply_to = message.reply_to_message
	if reply_to and reply_to.reply_markup and isinstance(reply_to.reply_markup, ReplyKeyboardMarkup):
		return await edit_or_reply(message, "`[!] → ` Not allowed from ReplyKeyboard")
	timeout = float(message.command["timeout"] or 60)
	args = message.command.text
	msg = await edit_or_reply(message, "` → ` Running")
	try:
		logger.info("Executing shell command \"%s\"", args)
		proc = await asyncio.create_subprocess_shell(
			args,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.STDOUT)
		stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout)
		result = cleartermcolor(stdout.decode()).rstrip()
		if len(args) + len(result) > 4080:
			await msg.edit(f"`$` `{args}`\n` → Output too long, sending as file`")
			prog = ProgressChatAction(client, message.chat.id)
			out = io.BytesIO((f"$ {args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out, progress=prog.tick)
		else:
			output = f"$ {args}"
			entities = [ MessageEntity(type=MessageEntityType.PRE, offset=0, length=len(output), language="bash") ]
			if len(result) > 0:
				entities.append(MessageEntity(type=MessageEntityType.PRE, offset=len(output) + 2, length=len(result), language="bash"))
				output += "\n\n" + result
			await msg.edit(output, entities=entities)
	except asyncio.exceptions.TimeoutError:
		await msg.edit(f"`$` `{args}`\n`[!] → ` Timed out")
	except Exception as e:
		logger.exception("Error in .run command")
		await msg.edit(f"`$ {args}`\n`[!] → ` " + str(e))

@HELP.add(cmd="<expr>")
@alemiBot.on_message(sudo & filterCommand(["eval", "e"]))
@set_offline
@cancel_chat_action
async def eval_cmd(client:alemiBot, message:Message):
	"""eval a python expression

	No imports can be made nor variables can be assigned :`eval` cannot have side effects.
	Returned value will be printed upon successful evaluation. `stdout` won't be captured (use `.ex`).
	If a coroutine is returned, it will be awaited. This won't tokenize large outputs per-line,	use .ex if you need that.
	"""
	reply_to = message.reply_to_message
	if reply_to and reply_to.reply_markup and isinstance(reply_to.reply_markup, ReplyKeyboardMarkup):
		return await edit_or_reply(message, "`[!] → ` Not allowed from ReplyKeyboard")
	args = message.command.text
	msg = await edit_or_reply(message, "` → ` Evaluating")
	try:
		logger.info("Evaluating \"%s\"", args)
		result = eval(args)
		if inspect.iscoroutine(result):
			result = await result
		result = str(result).rstrip()
		if len(args) + len(result) > 4080:
			await msg.edit(f"```>>> {args}\n → Output too long, sending as file```")
			prog = ProgressChatAction(client, message.chat.id)
			out = io.BytesIO((f">>> {args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out, parse_mode=ParseMode.MARKDOWN, progress=prog.tick)
		else:
			output = f">>> {args}"
			entities = [ MessageEntity(type=MessageEntityType.CODE, offset=0, length=len(output)) ]
			if len(result) > 0:
				entities.append(MessageEntity(type=MessageEntityType.CODE, offset=len(output), length=len(result) + 1))
				output += "\n" + result
			await msg.edit(output, entities=entities)
	except Exception as e:
		logger.exception("Error in .eval command")
		await msg.edit(f"`>>> {args}`\n`[!] {type(e).__name__} → ` {str(e)}", parse_mode=ParseMode.MARKDOWN)

async def aexec(code, client, message): # client and message are passed so they are in scope
	exec(
		f'async def __aex(): ' +
		''.join(f'\n {l}' for l in code.split('\n')),
		locals()
	)
	return await locals()['__aex']()

@HELP.add(cmd="<code>")
@alemiBot.on_message(sudo & filterCommand(["exec", "ex"]))
@set_offline
@cancel_chat_action
async def exec_cmd(client:alemiBot, message:Message):
	"""execute python code

	Will capture and print stdout.
	This, unlike `eval`, has no bounds and **can have side effects**. Use with more caution than `eval`!
	The `exec` call is wrapped to make it work with async code.
	"""
	reply_to = message.reply_to_message
	if reply_to and reply_to.reply_markup and isinstance(reply_to.reply_markup, ReplyKeyboardMarkup):
		return await edit_or_reply(message, "`[!] → ` Not allowed from ReplyKeyboard")
	args = message.command.text
	fancy_args = args.replace("\n", "\n... ")
	msg = message if is_me(message) else await message.reply("`[PLACEHOLDER]`")
	await msg.edit("```>>> " + fancy_args + "```\n` → ` Executing")
	try:
		logger.info("Executing python expr:\n\t%s", args.replace('\n', '\n\t'))
		with stdoutWrapper() as fake_stdout:
			await aexec(args, client, message)
		result = fake_stdout.getvalue().rstrip()
		if len(args) + len(result) > 4080:
			await msg.edit(f"`>>>` `{fancy_args}`\n` → Output too long, sending as file`")
			prog = ProgressChatAction(client, message.chat.id)
			out = io.BytesIO((f">>> {fancy_args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out, parse_mode=ParseMode.MARKDOWN, progress=prog.tick)
		else:
			output = f">>> {fancy_args}"
			entities = [ MessageEntity(type=MessageEntityType.PRE, offset=0, length=len(output), language="python") ]
			if len(result) > 0:
				entities.append(MessageEntity(type=MessageEntityType.PRE, offset=len(output) + 2, length=len(result), language="python"))
				output += "\n\n" + result
			await msg.edit(output, entities=entities)
	except Exception as e:
		logger.exception("Error in .exec command")
		await msg.edit(f"`>>> {args}`\n`[!] {type(e).__name__} → ` {str(e)}", parse_mode=ParseMode.MARKDOWN)

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("where", flags=["-no"]))
@report_error(logger)
@set_offline
@cancel_chat_action
async def where_cmd(client:alemiBot, message:Message):
	"""get info about a chat

	Get complete information about a chat and send it as json.
	If no chat name	or id is specified, current chat will be used.
	Add `-no` at the end if you just want the id : no file will be attached.
	"""
	tgt = message.chat
	prog = ProgressChatAction(client, message.chat.id)
	if len(message.command) > 0:
		arg = message.command[0]
		if arg.isnumeric():
			tgt = await client.get_chat(int(arg))
		else:
			tgt = await client.get_chat(arg)
	await edit_or_reply(message, f"` → ` Getting data of chat `{tgt.id}`")
	if not message.command["-no"]:
		out = io.BytesIO((str(tgt)).encode('utf-8'))
		out.name = f"chat-{tgt.id}.json"
		await client.send_document(message.chat.id, out, progress=prog.tick)

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("who", flags=["-no"]))
@report_error(logger)
@set_offline
@cancel_chat_action
async def who_cmd(client:alemiBot, message:Message):
	"""get info about a user

	Get complete information about user and attach as json.
	If replying to a message, author will be used.
	An id or @ can be specified. If neither is applicable, self will be used.
	Use `-no` flag if you just want the id.
	"""
	peer = get_user(message)
	prog = ProgressChatAction(client, message.chat.id)
	if len(message.command) > 0:
		arg = message.command[0]
		peer = await client.get_users(int(arg) if arg.isnumeric() else arg)
	elif message.reply_to_message is not None:
		peer = get_user(message.reply_to_message)
	await edit_or_reply(message, f"` → ` Getting data of user `{peer.id}`")
	if not message.command["-no"]:
		out = io.BytesIO((str(peer)).encode('utf-8'))
		out.name = f"user-{peer.id}.json"
		await client.send_document(message.chat.id, out, progress=prog.tick)

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("what", options={
	"group" : ["-g", "-group"]
}, flags=["-no"]))
@report_error(logger)
@set_offline
@cancel_chat_action
async def what_cmd(client:alemiBot, message:Message):
	"""get info about a message

	Get complete information about a message and attach as json.
	If replying, replied message will be used.
	Id and chat can be passed as arguments. If no chat is specified with `-g`, message will be searched in current chat.
	Append `-no` if you just want the id.
	"""
	msg = message
	prog = ProgressChatAction(client, message.chat.id)
	if message.reply_to_message is not None:
		msg = await client.get_messages(message.chat.id, message.reply_to_message.id)
	elif len(message.command) > 0 and message.command[0].isnumeric():
		chat_id = message.chat.id
		if "group" in message.command:
			if message.command["group"].isnumeric():
				chat_id = int(message.command["group"])
			else:
				chat_id = (await client.get_chat(message.command["group"])).id
		msg = await client.get_messages(chat_id, int(message.command[0]))
	await edit_or_reply(message, f"` → ` Getting data of msg `{msg.id}`")
	if not message.command["-no"]:
		out = io.BytesIO((str(msg)).encode('utf-8'))
		out.name = f"msg-{msg.id}.json"
		await client.send_document(message.chat.id, out, progress=prog.tick)

@HELP.add()
@alemiBot.on_message(sudo & filterCommand(["joined", "jd"]))
@report_error(logger)
@set_offline
@cancel_chat_action
async def joined_cmd(client:alemiBot, message:Message):
	"""count active chats

	Get number of dialogs: groups, supergroups, channels, dms, bots.
	Will show them divided by category and a total.
	"""
	msg = await edit_or_reply(message, "` → ` Counting...")
	res = {}
	total = 0
	with ProgressChatAction(client, message.chat.id, "choose_contact") as prog:
		async for dialog in client.iter_dialogs():
			if dialog.chat.type not in res:
				res[dialog.chat.type] = 0
			res[dialog.chat.type] += 1
			total += 1
	out = f"`→ ` **{total}** --Active chats-- \n"
	for k in res:
		out += f"` → ` **{k}** {res[k]}\n"
	await msg.edit(out)

@HELP.add()
@alemiBot.on_message(sudo & filterCommand(['tasks', 'task']))
@report_error(logger)
@set_offline
async def running_tasks_cmd(client:alemiBot, message:Message):
	"""show running callbacks
	
	Will print running handler callbacks, with their hash.
	To be able to use these functions, you need my (experimental!) pyrogram fork : 
	  pip install https://github.com/alemidev/pyrogram/archive/task_management.zip"""
	if not hasattr(client, "running"): # ugly check eww
		return await edit_or_reply(message, "<code>[!] → </code> This pyrogram version lacks task management.", parse_mode=ParseMode.HTML)
	line = "<b>[</b><code>{hash}</code><b>]</b> {name}\n"
	out = ""
	for h in client.running:
		out += line.format(hash=h, name=client.running[h].__name__)
	await edit_or_reply(message, out, parse_mode=ParseMode.HTML)

@HELP.add(cmd="<hash>")
@alemiBot.on_message(sudo & filterCommand(['stop', 'cancel']))
@report_error(logger)
@set_offline
async def cancel_task_cmd(client:alemiBot, message:Message):
	"""cancel running callbacks
	
	Will immediately stop a running callback.
	To be able to use these functions, you need my (experimental!) pyrogram fork : 
	  pip install https://github.com/alemidev/pyrogram/archive/task_management.zip"""
	if not hasattr(client, "running"): # ugly check eww
		return await edit_or_reply(message, "<code>[!] → </code> This pyrogram version lacks task management.", parse_mode=ParseMode.HTML)
	if len(message.command) < 1:
		return await edit_or_reply(message, "<code>[!] → </code> No task hash provided", parse_mode=ParseMode.HTML)
	cb_id = int(message.command[0])
	client.running.pop(cb_id).close()
	await edit_or_reply(message, f"<code> → </code> Canceled task <code>{cb_id}</code>", parse_mode=ParseMode.HTML)

@alemiBot.on_message(sudo & filterCommand(["make_botfather_list"], flags=["-all"]))
@report_error(logger)
@set_offline
async def botfather_list_command(client:alemiBot, message:Message):
	"""make botfather-compatible command list"""
	out = ""
	for k in CATEGORIES:
		for kk in CATEGORIES[k].HELP_ENTRIES:
			if not message.command["-all"] and not CATEGORIES[k].HELP_ENTRIES[kk].public:
				continue
			e = CATEGORIES[k].HELP_ENTRIES[kk]
			out += f"{e.title} - {e.args} | {e.shorttext}\n"
	await message.reply(out, parse_mode=ParseMode.MARKDOWN)
