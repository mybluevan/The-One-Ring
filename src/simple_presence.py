import logging

import dbus
import telepathy

import util.misc as misc_utils
import tp
import handle
import gvoice.state_machine as state_machine


_moduleLogger = logging.getLogger(__name__)


class TheOneRingPresence(object):

	# Note: these strings are also in the theonering.profile file
	ONLINE = 'available'
	AWAY = 'away'
	HIDDEN = 'hidden'
	OFFLINE = 'offline'

	TO_PRESENCE_TYPE = {
		ONLINE: dbus.UInt32(telepathy.constants.CONNECTION_PRESENCE_TYPE_AVAILABLE),
		AWAY: dbus.UInt32(telepathy.constants.CONNECTION_PRESENCE_TYPE_AWAY),
		HIDDEN: dbus.UInt32(telepathy.constants.CONNECTION_PRESENCE_TYPE_HIDDEN),
		OFFLINE: dbus.UInt32(telepathy.constants.CONNECTION_PRESENCE_TYPE_OFFLINE),
	}

	def __init__(self, ignoreDND):
		self.__ignoreDND = ignoreDND

	@property
	def session(self):
		"""
		@abstract
		"""
		raise NotImplementedError()

	def Disconnect(self):
		"""
		@abstract
		"""
		raise NotImplementedError("Abstract function called")

	def get_handle_by_id(self, handleType, handleId):
		"""
		@abstract
		"""
		raise NotImplementedError("Abstract function called")

	def get_presences(self, contactIds):
		"""
		@return {ContactHandle: (Status, Presence Type, Message)}
		"""
		presences = {}
		for handleId in contactIds:
			h = self.get_handle_by_id(telepathy.HANDLE_TYPE_CONTACT, handleId)
			if isinstance(h, handle.ConnectionHandle):
				isDnd = self.session.is_dnd() if not self.__ignoreDND else False
				if isDnd:
					presence = TheOneRingPresence.HIDDEN
				else:
					state = self.session.stateMachine.state
					if state == state_machine.StateMachine.STATE_ACTIVE:
						presence = TheOneRingPresence.ONLINE
					elif state == state_machine.StateMachine.STATE_IDLE:
						presence = TheOneRingPresence.AWAY
					else:
						raise telepathy.errors.InvalidArgument("Unsupported state on the state machine: %s" % state)
				presenceType = TheOneRingPresence.TO_PRESENCE_TYPE[presence]
			else:
				presence = TheOneRingPresence.AWAY
				presenceType = TheOneRingPresence.TO_PRESENCE_TYPE[presence]

			presences[h] = (presenceType, presence)
		return presences

	def set_presence(self, status):
		if status == self.ONLINE:
			if not self.__ignoreDND:
				self.session.set_dnd(False)
			self.session.stateMachine.set_state(state_machine.StateMachine.STATE_ACTIVE)
		elif status == self.AWAY:
			self.session.stateMachine.set_state(state_machine.StateMachine.STATE_IDLE)
		elif status == self.HIDDEN:
			if not self.__ignoreDND:
				self.session.set_dnd(True)
		elif status == self.OFFLINE:
			self.Disconnect()
		else:
			raise telepathy.errors.InvalidArgument("Unsupported status: %r" % status)
		_moduleLogger.info("Setting Presence to '%s'" % status)


class SimplePresenceMixin(tp.ConnectionInterfaceSimplePresence):

	def __init__(self, torPresence):
		tp.ConnectionInterfaceSimplePresence.__init__(self)
		self.__torPresence = torPresence

		self._implement_property_get(
			tp.CONNECTION_INTERFACE_SIMPLE_PRESENCE,
			{'Statuses' : self._get_statuses}
		)

	@misc_utils.log_exception(_moduleLogger)
	def GetPresences(self, contacts):
		"""
		@return {ContactHandle: (Status, Presence Type, Message)}
		"""
		personalMessage = u""
		return dbus.Dictionary(
			(
				(h, dbus.Struct((presenceType, presence, personalMessage), signature="uss"))
				for (h, (presenceType, presence)) in
					self.__torPresence.get_presences(contacts).iteritems()
			),
			signature="u(uss)"
		)

	@misc_utils.log_exception(_moduleLogger)
	def SetPresence(self, status, message):
		if message:
			raise telepathy.errors.InvalidArgument("Messages aren't supported")

		self.__torPresence.set_presence(status)

	def _get_statuses(self):
		"""
		Property mapping presence statuses available to the corresponding presence types

		@returns {Name: (Telepathy Type, May Set On Self, Can Have Message)}
		"""
		return dict(
			(localType, (telepathyType, True, False))
			for (localType, telepathyType) in self.__torPresence.TO_PRESENCE_TYPE.iteritems()
		)
