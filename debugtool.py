import asyncio
import io
import logging
import sys
import inspect

from bot import alemiBot

from pyrogram.types import MessageEntity

from util.command import filterCommand
from util.text import cleartermcolor
from util.getters import get_user
from util.message import is_me, edit_or_reply
from util.permission import is_superuser, is_allowed
from util.decorators import report_error, set_offline
from util.help import HelpCategory, CATEGORIES

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

@HELP.add()
@alemiBot.on_message(is_superuser & filterCommand("put", list(alemiBot.prefixes)))
@report_error(logger)
@set_offline
async def put_cmd(client, message):
	"""save file to server

	Reply to a media message or attach media to store file on server
	"""
	msg = message
	if message.reply_to_message is not None:
		msg = message.reply_to_message
	if msg.media:
		fpath = await client.download_media(msg)
		await edit_or_reply(message, '` → ` saved file as {}'.format(fpath))
	else:
		await edit_or_reply(message, "`[!] → ` No file")

@HELP.add(cmd="<path>")
@alemiBot.on_message(is_superuser & filterCommand("get", list(alemiBot.prefixes), flags=["-log"]))
@report_error(logger)
@set_offline
async def get_cmd(client, message):
	"""request a file from server

	Will upload a file from server to this chat.
	The path can be absolute or relative (starting from alemiBot workdir).
	Use flag `-log` to automatically upload `data/debug.log`.
	"""
	if len(message.command) < 1 and not message.command["-log"]:
		return await edit_or_reply(message, "`[!] → ` No input")
	await client.send_chat_action(message.chat.id, "upload_document")
	if message.command["-log"]: # this is handy for logger module!
		name = "data/debug.log" 
	else:
		name = message.command[0]
	await client.send_document(message.chat.id, name, reply_to_message_id=message.message_id, caption=f'` → {name}`')
	await client.send_chat_action(message.chat.id, "cancel")

@HELP.add(cmd="<cmd>")
@alemiBot.on_message(is_superuser & filterCommand(["run", "r"], list(alemiBot.prefixes), options={
	"timeout" : ["-t", "-time"]
}))
@set_offline
async def run_cmd(client, message):
	"""run a command on server

	Shell will be from user running bot: every command starts in bot root folder.
	There is a timeout of 60 seconds to any command issued, this can be changed with the `-t` option.
	You should properly wrap your arguments with `\"`, they will be ignored by cmd parser.
	"""
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
			out = io.BytesIO((f"$ {args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out)
		else:
			output = f"$ {args}"
			entities = [ MessageEntity(type="code", offset=0, length=len(output)) ]
			if len(result) > 0:
				entities.append(MessageEntity(type="pre", offset=len(output) + 2, length=len(result), language="bash"))
				output += "\n\n" + result
			await msg.edit(output, entities=entities)
	except asyncio.exceptions.TimeoutError:
		await msg.edit(f"`$` `{args}`\n`[!] → ` Timed out")
	except Exception as e:
		logger.exception("Error in .run command")
		await msg.edit(f"`$ {args}`\n`[!] → ` " + str(e))

@HELP.add(cmd="<expr>")
@alemiBot.on_message(is_superuser & filterCommand(["eval", "e"], list(alemiBot.prefixes)))
@set_offline
async def eval_cmd(client, message):
	"""eval a python expression

	No imports can be made nor variables can be assigned :`eval` cannot have side effects.
	Returned value will be printed upon successful evaluation. `stdout` won't be captured (use `.ex`).
	If a coroutine is returned, it will be awaited. This won't tokenize large outputs per-line,	use .ex if you need that.
	"""
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
			out = io.BytesIO((f">>> {args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out, parse_mode="markdown")
		else:
			output = f">>> {args}"
			entities = [ MessageEntity(type="code", offset=0, length=len(output)) ]
			if len(result) > 0:
				entities.append(MessageEntity(type="code", offset=len(output), length=len(result) + 1))
				output += "\n" + result
			await msg.edit(output, entities=entities)
	except Exception as e:
		logger.exception("Error in .eval command")
		await msg.edit(f"`>>>` `{args}`\n`[!] → ` " + str(e), parse_mode='markdown')

async def aexec(code, client, message): # client and message are passed so they are in scope
	exec(
		f'async def __aex(): ' +
		''.join(f'\n {l}' for l in code.split('\n')),
		locals()
	)
	return await locals()['__aex']()

@HELP.add(cmd="<code>")
@alemiBot.on_message(is_superuser & filterCommand(["exec", "ex"], list(alemiBot.prefixes)))
@set_offline
async def exec_cmd(client, message):
	"""execute python code

	Will capture and print stdout.
	This, unlike `eval`, has no bounds and **can have side effects**. Use with more caution than `eval`!
	The `exec` call is wrapped to make it work with async code.
	"""
	args = message.command.text
	fancy_args = args.replace("\n", "\n... ")
	msg = message if is_me(message) else await message.reply("`[PLACEHOLDER]`")
	await msg.edit("```" + fancy_args + "```\n` → ` Executing")
	try:
		logger.info("Executing python expr \"%s\"", args)
		with stdoutWrapper() as fake_stdout:
			await aexec(args, client, message)
		result = fake_stdout.getvalue().rstrip()
		if len(args) + len(result) > 4080:
			await msg.edit(f"`>>>` `{fancy_args}`\n` → Output too long, sending as file`")
			out = io.BytesIO((f">>> {fancy_args}\n" + result).encode('utf-8'))
			out.name = "output.txt"
			await client.send_document(message.chat.id, out, parse_mode='markdown')
		else:
			output = f">>> {fancy_args}"
			entities = [ MessageEntity(type="pre", offset=0, length=len(output), language="python") ]
			if len(result) > 0:
				entities.append(MessageEntity(type="pre", offset=len(output) + 2, length=len(result), language="python"))
				output += "\n\n" + result
			await msg.edit(output, entities=entities)
	except Exception as e:
		logger.exception("Error in .exec command")
		await msg.edit(f"`>>> {args}`\n`[!] → ` " + str(e), parse_mode='markdown')

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("where", list(alemiBot.prefixes), flags=["-no"]))
@report_error(logger)
@set_offline
async def where_cmd(client, message):
	"""get info about a chat

	Get complete information about a chat and send it as json.
	If no chat name	or id is specified, current chat will be used.
	Add `-no` at the end if you just want the id : no file will be attached.
	"""
	tgt = message.chat
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
		await client.send_document(message.chat.id, out)

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("who", list(alemiBot.prefixes), flags=["-no"]))
@report_error(logger)
@set_offline
async def who_cmd(client, message):
	"""get info about a user

	Get complete information about user and attach as json.
	If replying to a message, author will be used.
	An id or @ can be specified. If neither is applicable, self will be used.
	Use `-no` flag if you just want the id.
	"""
	peer = get_user(message)
	if len(message.command) > 0:
		arg = message.command[0]
		peer = await client.get_users(int(arg) if arg.isnumeric() else arg)
	elif message.reply_to_message is not None:
		peer = get_user(message.reply_to_message)
	await edit_or_reply(message, f"` → ` Getting data of user `{peer.id}`")
	if not message.command["-no"]:
		out = io.BytesIO((str(peer)).encode('utf-8'))
		out.name = f"user-{peer.id}.json"
		await client.send_document(message.chat.id, out)

@HELP.add(cmd="[<target>]", sudo=False)
@alemiBot.on_message(is_allowed & filterCommand("what", list(alemiBot.prefixes), options={
	"group" : ["-g", "-group"]
}, flags=["-no"]))
@report_error(logger)
@set_offline
async def what_cmd(client, message):
	"""get info about a message

	Get complete information about a message and attach as json.
	If replying, replied message will be used.
	Id and chat can be passed as arguments. If no chat is specified with `-g`, message will be searched in current chat.
	Append `-no` if you just want the id.
	"""
	msg = message
	if message.reply_to_message is not None:
		msg = await client.get_messages(message.chat.id, message.reply_to_message.message_id)
	elif len(message.command) > 0 and message.command[0].isnumeric():
		chat_id = message.chat.id
		if "group" in message.command:
			if message.command["group"].isnumeric():
				chat_id = int(message.command["group"])
			else:
				chat_id = (await client.get_chat(message.command["group"])).id
		msg = await client.get_messages(chat_id, int(message.command[0]))
	await edit_or_reply(message, f"` → ` Getting data of msg `{msg.message_id}`")
	if not message.command["-no"]:
		out = io.BytesIO((str(msg)).encode('utf-8'))
		out.name = f"msg-{msg.message_id}.json"
		await client.send_document(message.chat.id, out)

@HELP.add()
@alemiBot.on_message(is_superuser & filterCommand(["joined", "jd"], list(alemiBot.prefixes)))
@report_error(logger)
@set_offline
async def joined_cmd(client, message):
	"""count active chats

	Get number of dialogs: groups, supergroups, channels, dms, bots.
	Will show them divided by category and a total.
	"""
	msg = await edit_or_reply(message, "` → ` Counting...")
	res = {}
	total = 0
	async for dialog in client.iter_dialogs():
		if dialog.chat.type not in res:
			res[dialog.chat.type] = 0
		res[dialog.chat.type] += 1
		total += 1
	out = "`→ ` **{total}** --Active chats-- \n"
	for k in res:
		out += f"` → ` **{k}** {res[k]}\n"
	await msg.edit(out)

@alemiBot.on_message(is_superuser & filterCommand(["make_botfather_list"], list(alemiBot.prefixes)))
@report_error(logger)
@set_offline
async def botfather_list_command(client, message):
	"""make botfather-compatible command list"""
	out = ""
	for k in CATEGORIES:
		for kk in CATEGORIES[k].HELP_ENTRIES:
			e = CATEGORIES[k].HELP_ENTRIES[kk]
			out += f"{e.title} - {e.args} | {e.shorttext}\n"
	await message.reply(out, parse_mode='markdown')
