import os
import weakref
import logging

import telepathy

try:
	import conic as _conic
	conic = _conic
except (ImportError, OSError):
	conic = None

import constants
import tp
import util.coroutines as coroutines
import util.misc as util_misc
import gtk_toolbox

import gvoice
import handle

import requests
import contacts
import aliasing
import simple_presence
import presence
import capabilities

import channel_manager


_moduleLogger = logging.getLogger("connection")


class TheOneRingOptions(object):

	useGVContacts = True

	assert gvoice.session.Session._DEFAULTS["contacts"][1] == "hours"
	contactsPollPeriodInHours = gvoice.session.Session._DEFAULTS["contacts"][0]

	assert gvoice.session.Session._DEFAULTS["voicemail"][1] == "minutes"
	voicemailPollPeriodInMinutes = gvoice.session.Session._DEFAULTS["voicemail"][0]

	assert gvoice.session.Session._DEFAULTS["texts"][1] == "minutes"
	textsPollPeriodInMinutes = gvoice.session.Session._DEFAULTS["texts"][0]

	def __init__(self, parameters = None):
		if parameters is None:
			return
		self.useGVContacts = parameters["use-gv-contacts"]
		self.contactsPollPeriodInHours = parameters['contacts-poll-period-in-hours']
		self.voicemailPollPeriodInMinutes = parameters['voicemail-poll-period-in-minutes']
		self.textsPollPeriodInMinutes = parameters['texts-poll-period-in-minutes']


class TheOneRingConnection(
	tp.Connection,
	requests.RequestsMixin,
	contacts.ContactsMixin,
	aliasing.AliasingMixin,
	simple_presence.SimplePresenceMixin,
	presence.PresenceMixin,
	capabilities.CapabilitiesMixin,
):

	# overiding base class variable
	_mandatory_parameters = {
		'account': 's',
		'password': 's',
	}
	# overiding base class variable
	_optional_parameters = {
		'forward': 's',
		'use-gv-contacts': 'b',
		'contacts-poll-period': 'i',
		'voicemail-poll-period': 'i',
		'texts-poll-period': 'i',
	}
	_parameter_defaults = {
		'forward': '',
		'use-gv-contacts': TheOneRingOptions.useGVContacts,
		'contacts-poll-period-in-hours': TheOneRingOptions.contactsPollPeriodInHours,
		'voicemail-poll-period-in-minutes': TheOneRingOptions.voicemailPollPeriodInMinutes,
		'texts-poll-period-in-minutes': TheOneRingOptions.textsPollPeriodInMinutes,
	}
	_secret_parameters = set((
		"password",
	))

	@gtk_toolbox.log_exception(_moduleLogger)
	def __init__(self, manager, parameters):
		self.check_parameters(parameters)
		account = unicode(parameters['account'])
		encodedAccount = parameters['account'].encode('utf-8')
		encodedPassword = parameters['password'].encode('utf-8')
		encodedCallback = util_misc.normalize_number(parameters['forward'].encode('utf-8'))
		if encodedCallback and not util_misc.is_valid_number(encodedCallback):
			raise telepathy.errors.InvalidArgument("Invalid forwarding number")

		# Connection init must come first
		self.__session = gvoice.session.Session(None)
		self.__options = TheOneRingOptions(parameters)
		tp.Connection.__init__(
			self,
			constants._telepathy_protocol_name_,
			account,
			constants._telepathy_implementation_name_
		)
		requests.RequestsMixin.__init__(self)
		contacts.ContactsMixin.__init__(self)
		aliasing.AliasingMixin.__init__(self)
		simple_presence.SimplePresenceMixin.__init__(self)
		presence.PresenceMixin.__init__(self)
		capabilities.CapabilitiesMixin.__init__(self)

		self.__manager = weakref.proxy(manager)
		self.__credentials = (
			encodedAccount,
			encodedPassword,
		)
		self.__callbackNumberParameter = encodedCallback
		self.__channelManager = channel_manager.ChannelManager(self)

		if conic is not None:
			self.__connection = conic.Connection()
		else:
			self.__connection = None
		self.__cachePath = os.sep.join((constants._data_path_, "cache", self.username))
		try:
			os.makedirs(self.__cachePath)
		except OSError, e:
			if e.errno != 17:
				raise

		self.set_self_handle(handle.create_handle(self, 'connection'))

		self.__callback = None
		_moduleLogger.info("Connection to the account %s created" % account)

	@property
	def manager(self):
		return self.__manager

	@property
	def session(self):
		return self.__session

	@property
	def options(self):
		return self.__options

	@property
	def username(self):
		return self.__credentials[0]

	@property
	def callbackNumberParameter(self):
		return self.__callbackNumberParameter

	def get_handle_by_name(self, handleType, handleName):
		requestedHandleName = handleName.encode('utf-8')
		if handleType == telepathy.HANDLE_TYPE_CONTACT:
			_moduleLogger.debug("get_handle_by_name Contact: %s" % requestedHandleName)
			h = handle.create_handle(self, 'contact', requestedHandleName)
		elif handleType == telepathy.HANDLE_TYPE_LIST:
			# Support only server side (immutable) lists
			_moduleLogger.debug("get_handle_by_name List: %s" % requestedHandleName)
			h = handle.create_handle(self, 'list', requestedHandleName)
		else:
			raise telepathy.errors.NotAvailable('Handle type unsupported %d' % handleType)
		return h

	@property
	def _channel_manager(self):
		return self.__channelManager

	@gtk_toolbox.log_exception(_moduleLogger)
	def Connect(self):
		"""
		For org.freedesktop.telepathy.Connection
		"""
		_moduleLogger.info("Connecting...")
		self.StatusChanged(
			telepathy.CONNECTION_STATUS_CONNECTING,
			telepathy.CONNECTION_STATUS_REASON_REQUESTED
		)
		try:
			self.__session.load(self.__cachePath)

			self.__callback = coroutines.func_sink(
				coroutines.expand_positional(
					self._on_conversations_updated
				)
			)
			self.session.voicemails.updateSignalHandler.register_sink(
				self.__callback
			)
			self.session.texts.updateSignalHandler.register_sink(
				self.__callback
			)
			self.session.login(*self.__credentials)
			if not self.__callbackNumberParameter:
				callback = gvoice.backend.get_sane_callback(
					self.session.backend
				)
				self.__callbackNumberParameter = util_misc.normalize_number(callback)
			self.session.backend.set_callback_number(self.__callbackNumberParameter)

			subscribeHandle = self.get_handle_by_name(telepathy.HANDLE_TYPE_LIST, "subscribe")
			subscribeProps = self._generate_props(telepathy.CHANNEL_TYPE_CONTACT_LIST, subscribeHandle, False)
			self.__channelManager.channel_for_props(subscribeProps, signal=True)
			publishHandle = self.get_handle_by_name(telepathy.HANDLE_TYPE_LIST, "publish")
			publishProps = self._generate_props(telepathy.CHANNEL_TYPE_CONTACT_LIST, publishHandle, False)
			self.__channelManager.channel_for_props(publishProps, signal=True)
		except gvoice.backend.NetworkError, e:
			_moduleLogger.exception("Connection Failed")
			self.StatusChanged(
				telepathy.CONNECTION_STATUS_DISCONNECTED,
				telepathy.CONNECTION_STATUS_REASON_NETWORK_ERROR
			)
			return
		except Exception, e:
			_moduleLogger.exception("Connection Failed")
			self.StatusChanged(
				telepathy.CONNECTION_STATUS_DISCONNECTED,
				telepathy.CONNECTION_STATUS_REASON_AUTHENTICATION_FAILED
			)
			return

		_moduleLogger.info("Connected")
		self.StatusChanged(
			telepathy.CONNECTION_STATUS_CONNECTED,
			telepathy.CONNECTION_STATUS_REASON_REQUESTED
		)
		if self.__connection is not None:
			self.__connectionEventId = self.__connection.connect("connection-event", self._on_connection_change)

	@gtk_toolbox.log_exception(_moduleLogger)
	def Disconnect(self):
		"""
		For org.freedesktop.telepathy.Connection
		"""
		try:
			self._disconnect()
		except Exception:
			_moduleLogger.exception("Error durring disconnect")
		self.StatusChanged(
			telepathy.CONNECTION_STATUS_DISCONNECTED,
			telepathy.CONNECTION_STATUS_REASON_REQUESTED
		)

	@gtk_toolbox.log_exception(_moduleLogger)
	def RequestChannel(self, type, handleType, handleId, suppressHandler):
		"""
		For org.freedesktop.telepathy.Connection

		@param type DBus interface name for base channel type
		@param handleId represents a contact, list, etc according to handleType

		@returns DBus object path for the channel created or retrieved
		"""
		self.check_connected()
		self.check_handle(handleType, handleId)

		h = self.get_handle_by_id(handleType, handleId) if handleId != 0 else None
		props = self._generate_props(type, h, suppressHandler)
		self._validate_handle(props)

		chan = self.__channelManager.channel_for_props(props, signal=True)
		path = chan._object_path
		_moduleLogger.info("RequestChannel Object Path (%s): %s" % (type.rsplit(".", 1)[-1], path))
		return path

	def _generate_props(self, channelType, handle, suppressHandler, initiatorHandle=None):
		targetHandle = 0 if handle is None else handle.get_id()
		targetHandleType = telepathy.HANDLE_TYPE_NONE if handle is None else handle.get_type()
		props = {
			telepathy.CHANNEL_INTERFACE + '.ChannelType': channelType,
			telepathy.CHANNEL_INTERFACE + '.TargetHandle': targetHandle,
			telepathy.CHANNEL_INTERFACE + '.TargetHandleType': targetHandleType,
			telepathy.CHANNEL_INTERFACE + '.Requested': suppressHandler
		}

		if initiatorHandle is not None:
			props[telepathy.CHANNEL_INTERFACE + '.InitiatorHandle'] = initiatorHandle.id

		return props

	def _disconnect(self):
		_moduleLogger.info("Disconnecting")
		self.session.voicemails.updateSignalHandler.unregister_sink(
			self.__callback
		)
		self.session.texts.updateSignalHandler.unregister_sink(
			self.__callback
		)
		self.__callback = None

		self.__channelManager.close()
		self.session.save(self.__cachePath)
		self.session.logout()
		self.session.close()

		self.manager.disconnected(self)
		_moduleLogger.info("Disconnected")

	@gtk_toolbox.log_exception(_moduleLogger)
	def _on_conversations_updated(self, conv, conversationIds):
		_moduleLogger.debug("Incoming messages from: %r" % (conversationIds, ))
		for phoneNumber in conversationIds:
			h = self.get_handle_by_name(telepathy.HANDLE_TYPE_CONTACT, phoneNumber)
			# Just let the TextChannel decide whether it should be reported to the user or not
			props = self._generate_props(telepathy.CHANNEL_TYPE_TEXT, h, False)
			if self.__channelManager.channel_exists(props):
				continue

			# Maemo 4.1's RTComm opens a window for a chat regardless if a
			# message is received or not, so we need to do some filtering here
			mergedConv = conv.get_conversation(phoneNumber)
			unreadConvs = [
				conversation
				for conversation in mergedConv.conversations
				if not conversation.isRead and not conversation.isArchived
			]
			if not unreadConvs:
				continue

			chan = self.__channelManager.channel_for_props(props, signal=True)

	@gtk_toolbox.log_exception(_moduleLogger)
	def _on_connection_change(self, connection, event):
		"""
		@note Maemo specific
		"""
		if not self.session.is_logged_in():
			_moduleLogger.info("Received connection change event when not logged in")
			return
		status = event.get_status()
		error = event.get_error()
		iap_id = event.get_iap_id()
		bearer = event.get_bearer_type()

		if status == conic.STATUS_DISCONNECTED:
			_moduleLogger.info("Disconnecting due to loss of network connection")
			self.StatusChanged(
				telepathy.CONNECTION_STATUS_DISCONNECTED,
				telepathy.CONNECTION_STATUS_REASON_NETWORK_ERROR
			)
			try:
				self._disconnect()
			except Exception:
				_moduleLogger.exception("Error durring disconnect")
		else:
			_moduleLogger.info("Other status: %r" % (status, ))
