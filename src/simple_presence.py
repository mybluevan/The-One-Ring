import logging

import telepathy

import gtk_toolbox
import handle
import gvoice.state_machine as state_machine


_moduleLogger = logging.getLogger("simple_presence")


class TheOneRingPresence(object):

	# Note: these strings are also in the theonering.profile file
	ONLINE = 'available'
	AWAY = 'away'
	HIDDEN = 'hidden'
	OFFLINE = 'offline'

	TO_PRESENCE_TYPE = {
		ONLINE: telepathy.constants.CONNECTION_PRESENCE_TYPE_AVAILABLE,
		AWAY: telepathy.constants.CONNECTION_PRESENCE_TYPE_AWAY,
		HIDDEN: telepathy.constants.CONNECTION_PRESENCE_TYPE_HIDDEN,
		OFFLINE: telepathy.constants.CONNECTION_PRESENCE_TYPE_OFFLINE,
	}


class SimplePresenceMixin(telepathy.server.ConnectionInterfaceSimplePresence):

	def __init__(self):
		telepathy.server.ConnectionInterfaceSimplePresence.__init__(self)

		self._implement_property_get(
			telepathy.server.CONNECTION_INTERFACE_SIMPLE_PRESENCE,
			{'Statuses' : self._get_statuses}
		)

	@property
	def session(self):
		"""
		@abstract
		"""
		raise NotImplementedError()

	@property
	def handle(self):
		"""
		@abstract
		"""
		raise NotImplementedError("Abstract property called")

	def Disconnect(self):
		"""
		@abstract
		"""
		raise NotImplementedError("Abstract function called")

	@gtk_toolbox.log_exception(_moduleLogger)
	def GetPresences(self, contacts):
		"""
		@return {ContactHandle: (Status, Presence Type, Message)}
		"""
		presences = {}
		for handleId in contacts:
			h = self.handle(telepathy.HANDLE_TYPE_CONTACT, handleId)
			if isinstance(h, handle.ConnectionHandle):
				isDnd = self.session.backend.is_dnd()
				if isDnd:
					presence = TheOneRingPresence.HIDDEN
				else:
					state = self.session.stateMachine.get_state()
					if state == state_machine.StateMachine.STATE_ACTIVE:
						presence = TheOneRingPresence.ONLINE
					elif state == state_machine.StateMachine.STATE_IDLE:
						presence = TheOneRingPresence.AWAY
					else:
						raise telepathy.errors.InvalidArgument("Unsupported state on the state machine: %s" % state)
				personalMessage = u""
				presenceType = TheOneRingPresence.TO_PRESENCE_TYPE[presence]
			else:
				presence = TheOneRingPresence.ONLINE
				personalMessage = u""
				presenceType = TheOneRingPresence.TO_PRESENCE_TYPE[presence]

			presences[h] = (presenceType, presence, personalMessage)
		return presences

	@gtk_toolbox.log_exception(_moduleLogger)
	def SetPresence(self, status, message):
		if message:
			raise telepathy.errors.InvalidArgument("Messages aren't supported")

		if status == TheOneRingPresence.ONLINE:
			self.session.backend.set_dnd(False)
			self.session.stateMachine.set_state(state_machine.StateMachine.STATE_ACTIVE)
		elif status == TheOneRingPresence.AWAY:
			self.session.stateMachine.set_state(state_machine.StateMachine.STATE_IDLE)
		elif status == TheOneRingPresence.HIDDEN:
			self.session.backend.set_dnd(True)
		elif status == TheOneRingPresence.OFFLINE:
			self.Disconnect()
		else:
			raise telepathy.errors.InvalidArgument("Unsupported status: %r" % status)
		_moduleLogger.info("Setting Presence to '%s'" % status)


	def _get_statuses(self):
		"""
		Property mapping presence statuses available to the corresponding presence types

		@returns {Name: (Telepathy Type, May Set On Self, Can Have Message)}
		"""
		return dict(
			(localType, (telepathyType, True, False))
			for (localType, telepathyType) in TheOneRingPresence.TO_PRESENCE_TYPE.iteritems()
		)
