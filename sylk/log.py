
"""
Logging support adapted from SIP SIMPLE Client logger.
"""

__all__ = ["Logger"]

import datetime
import os
import sys

from pprint import pformat

from application import log
from application.notification import IObserver, NotificationCenter
from application.python import Null
from application.system import makedirs
from sipsimple.configuration.settings import SIPSimpleSettings
from sipsimple.threading import run_in_thread
from zope.interface import implements


class Logger(object):
    implements(IObserver)

    # public methods
    #

    def __init__(self):
        self.stopped = False
        self.msrp_level = log.level.ERROR

        self._siptrace_filename = None
        self._siptrace_file = None
        self._siptrace_error = False
        self._siptrace_start_time = None
        self._siptrace_packet_count = 0

        self._msrptrace_filename = None
        self._msrptrace_file = None
        self._msrptrace_error = False

        self._pjsiptrace_filename = None
        self._pjsiptrace_file = None
        self._pjsiptrace_error = False

        self._notifications_filename = None
        self._notifications_file = None
        self._notifications_error = False

        self._log_directory_error = False

    def start(self):
        # try to create the log directory
        try:
            self._init_log_directory()
        except Exception:
            pass

        # register to receive log notifications
        NotificationCenter().add_observer(self)
        self.stopped = False

    def stop(self):
        NotificationCenter().remove_observer(self)
        self.stopped = False
        self._close_files()

    @run_in_thread('log-io')
    def _close_files(self):
        # close sip trace file
        if self._siptrace_file is not None:
            self._siptrace_file.close()
            self._siptrace_file = None

        # close msrp trace file
        if self._msrptrace_file is not None:
            self._msrptrace_file.close()
            self._msrptrace_file = None

        # close pjsip trace file
        if self._pjsiptrace_file is not None:
            self._pjsiptrace_file.close()
            self._pjsiptrace_file = None

        # close notifications trace file
        if self._notifications_file is not None:
            self._notifications_file.close()
            self._notifications_file = None

    def handle_notification(self, notification):
        if self.stopped:
            return
        self._process_notification(notification)

    @run_in_thread('log-io')
    def _process_notification(self, notification):
        settings = SIPSimpleSettings()
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

        handler = getattr(self, '_LH_%s' % notification.name, Null)
        handler(notification)

        if notification.name not in ('SIPEngineLog', 'SIPEngineSIPTrace') and settings.logs.trace_notifications:
            message = 'Notification name=%s sender=%s' % (notification.name, notification.sender)
            if notification.data is not None:
                message += '\n%s' % pformat(notification.data.__dict__)
            if settings.logs.trace_notifications:
                try:
                    self._init_log_file('notifications')
                except Exception:
                    pass
                else:
                    self._notifications_file.write('%s [%s %d]: %s\n' % (datetime.datetime.now(), os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
                    self._notifications_file.flush()

    # notification handlers
    #

    def _NH_CFGSettingsObjectDidChange(self, notification):
        settings = SIPSimpleSettings()
        if notification.sender is settings:
            if 'logs.directory' in notification.data.modified:
                # sip trace
                if self._siptrace_file is not None:
                    self._siptrace_file.close()
                    self._siptrace_file = None
                # pjsip trace
                if self._pjsiptrace_file is not None:
                    self._pjsiptrace_file.close()
                    self._pjsiptrace_file = None
                # notifications trace
                if self._notifications_file is not None:
                    self._notifications_file.close()
                    self._notifications_file = None
                # try to create the log directory
                try:
                    self._init_log_directory()
                except Exception:
                    pass

    # log handlers
    #

    def _LH_SIPEngineSIPTrace(self, notification):
        settings = SIPSimpleSettings()
        if not settings.logs.trace_sip:
            return
        if self._siptrace_start_time is None:
            self._siptrace_start_time = notification.datetime
        self._siptrace_packet_count += 1
        if notification.data.received:
            direction = "RECEIVED"
        else:
            direction = "SENDING"
        buf = ("%s: Packet %d, +%s" % (direction, self._siptrace_packet_count, (notification.datetime - self._siptrace_start_time)),
               "%(source_ip)s:%(source_port)d -(SIP over %(transport)s)-> %(destination_ip)s:%(destination_port)d" % notification.data.__dict__,
               notification.data.data,
               '--')
        message = '\n'.join(buf)
        try:
            self._init_log_file('siptrace')
        except Exception:
            pass
        else:
            self._siptrace_file.write('%s [%s %d]: %s\n' % (notification.datetime, os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
            self._siptrace_file.flush()

    def _LH_SIPEngineLog(self, notification):
        settings = SIPSimpleSettings()
        if not settings.logs.trace_pjsip:
            return
        message = "(%(level)d) %(message)s" % notification.data.__dict__
        try:
            self._init_log_file('pjsiptrace')
        except Exception:
            pass
        else:
            self._pjsiptrace_file.write('%s [%s %d] %s\n' % (notification.datetime, os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
            self._pjsiptrace_file.flush()

    def _LH_DNSLookupTrace(self, notification):
        settings = SIPSimpleSettings()
        if not settings.logs.trace_sip:
            return
        message = 'DNS lookup %(query_type)s %(query_name)s' % notification.data.__dict__
        if notification.data.error is None:
            message += ' succeeded, ttl=%d: ' % notification.data.answer.ttl
            if notification.data.query_type == 'A':
                message += ", ".join(record.address for record in notification.data.answer)
            elif notification.data.query_type == 'SRV':
                message += ", ".join('%d %d %d %s' % (record.priority, record.weight, record.port, record.target) for record in notification.data.answer)
            elif notification.data.query_type == 'NAPTR':
                message += ", ".join('%d %d "%s" "%s" "%s" %s' % (record.order, record.preference, record.flags, record.service, record.regexp, record.replacement) for record in notification.data.answer)
        else:
            import dns.resolver
            message_map = {dns.resolver.NXDOMAIN: 'DNS record does not exist',
                           dns.resolver.NoAnswer: 'DNS response contains no answer',
                           dns.resolver.NoNameservers: 'no DNS name servers could be reached',
                           dns.resolver.Timeout: 'no DNS response received, the query has timed out'}
            message += ' failed: %s' % message_map.get(notification.data.error.__class__, '')
        try:
            self._init_log_file('siptrace')
        except Exception:
            pass
        else:
            self._siptrace_file.write('%s [%s %d]: %s\n' % (notification.datetime, os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
            self._siptrace_file.flush()

    def _LH_MSRPTransportTrace(self, notification):
        settings = SIPSimpleSettings()
        if not settings.logs.trace_msrp:
            return
        arrow = {'incoming': '<--', 'outgoing': '-->'}[notification.data.direction]
        local_address = notification.sender.getHost()
        local_address = '%s:%d' % (local_address.host, local_address.port)
        remote_address = notification.sender.getPeer()
        remote_address = '%s:%d' % (remote_address.host, remote_address.port)
        message = '%s %s %s\n' % (local_address, arrow, remote_address) + notification.data.data
        try:
            self._init_log_file('msrptrace')
        except Exception:
            pass
        else:
            self._msrptrace_file.write('%s [%s %d]: %s\n' % (notification.datetime, os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
            self._msrptrace_file.flush()

    def _LH_MSRPLibraryLog(self, notification):
        settings = SIPSimpleSettings()
        if not settings.logs.trace_msrp:
            return
        if notification.data.level < self.msrp_level:
            return
        message = '%s%s' % (notification.data.level.prefix, notification.data.message)
        try:
            self._init_log_file('msrptrace')
        except Exception:
            pass
        else:
            self._msrptrace_file.write('%s [%s %d]: %s\n' % (notification.datetime, os.path.basename(sys.argv[0]).rstrip('.py'), os.getpid(), message))
            self._msrptrace_file.flush()

    # private methods
    #

    def _init_log_directory(self):
        settings = SIPSimpleSettings()
        log_directory = settings.logs.directory.normalized
        try:
            makedirs(log_directory)
        except Exception, e:
            if not self._log_directory_error:
                print "failed to create logs directory '%s': %s" % (log_directory, e)
                self._log_directory_error = True
            self._siptrace_error = True
            self._pjsiptrace_error = True
            self._notifications_error = True
            raise
        else:
            self._log_directory_error = False
            # sip trace
            if self._siptrace_filename is None:
                self._siptrace_filename = os.path.join(log_directory, 'sip_trace.log')
                self._siptrace_error = False

            # msrp trace
            if self._msrptrace_filename is None:
                self._msrptrace_filename = os.path.join(log_directory, 'msrp_trace.log')
                self._msrptrace_error = False

            # pjsip trace
            if self._pjsiptrace_filename is None:
                self._pjsiptrace_filename = os.path.join(log_directory, 'core_trace.log')
                self._pjsiptrace_error = False

            # notifications trace
            if self._notifications_filename is None:
                self._notifications_filename = os.path.join(log_directory, 'notifications_trace.log')
                self._notifications_error = False

    def _init_log_file(self, type):
        if getattr(self, '_%s_file' % type) is None:
            self._init_log_directory()
            filename = getattr(self, '_%s_filename' % type)
            try:
                setattr(self, '_%s_file' % type, open(filename, 'a'))
            except Exception, e:
                if not getattr(self, '_%s_error' % type):
                    print "failed to create log file '%s': %s" % (filename, e)
                    setattr(self, '_%s_error' % type, True)
                raise
            else:
                setattr(self, '_%s_error' % type, False)

