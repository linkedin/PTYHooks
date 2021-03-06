#!/usr/bin/env python
# Copyright 2016 LinkedIn Corp. Licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License.
#
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
"""
Usage: ptyhooks [-c CONFIG_MODULE] [-h] [--help] [COMMAND [ARGUMENTS...]]

Execute Python functions in response to output generated by a subprocess. When
no subprocess COMMAND is specified, PTYHooks will attempt to launch the
application defined by the SHELL environment variable and defaults to executing
"/bin/sh" when that is unset.

Argument parsing is handled using traditional getopt(3) instead of GNU
getopt(3). If PTYHooks used GNU getopt(3), "ptyhooks bash -l" would not work
because PTYHooks would try to parse "-l" as though it were intended for itself
instead of Bash.

Options:
 -c CONFIG_MODULE   Load specified file as the configuration module. When
                    unspecified, PTYHooks will try to use "$HOME/.ptyhooks.py"
                    as the configuration module.
 -h, --help         Show this text and exit.
"""
from __future__ import print_function

import array
import errno
import fcntl
import getopt
import imp
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import tty

STDIN_FILENO = sys.stdin.fileno()
STDOUT_FILENO = sys.stdout.fileno()
STDERR_FILENO = sys.stderr.fileno()

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.ptyhooks.py")


# Prior to Python 3.5, low-level syscalls need to be explicitly retried when
# interrupted. Refer to PEP-0475 for more information about interrupted system
# calls: https://www.python.org/dev/peps/pep-0475/#interrupted-system-calls
if sys.version_info < (3, 5):
    def eintr_protect(function):
        """
        Wrap a function so that execution is retried when interrupted by EINTR.
        """
        def wrapped_function(*args, **kwargs):
            while True:
                try:
                    return function(*args, **kwargs)
                except EnvironmentError as exc:
                    if exc.errno != errno.EINTR:
                        raise

        wrapped_function.__doc__ = function.__doc__
        return wrapped_function

    read = eintr_protect(os.read)
    _write = eintr_protect(os.write)

else:
    read = os.read
    _write = os.write

if sys.version_info >= (3, ):
    # For this script's purposes, buffer and memoryview objects are
    # interchangeable.
    buffer = memoryview
else:
    basestring = str


def copy_winsize(target_fd, source_fd=STDIN_FILENO):
    """
    Propagate terminal size from `source_fd` to `target_fd`.
    """
    # Create a buffer to store terminal size attributes. Refer to tty_ioctl(4)
    # for more information.
    winsize = array.array("h", [
        0,  # unsigned short ws_row
        0,  # unsigned short ws_col
        0,  # unsigned short ws_xpixel (unused)
        0,  # unsigned short ws_ypixel (unused)
    ])

    # Write window size into winsize variable.
    fcntl.ioctl(source_fd, termios.TIOCGWINSZ, winsize, True)
    # Send winsize to target terminal.
    fcntl.ioctl(target_fd, termios.TIOCSWINSZ, winsize)


def write(fd, data):
    """
    Write all data given to specified file descriptor. This function handles
    incomplete writes automatically.
    """
    remaining = len(data)
    data = buffer(data)
    while remaining > 0:
        remaining -= _write(fd, data[-remaining:])


def main(argv, input_hooks, output_hooks, maxread=4096):
    """
    Create subprocess using `argv` and execute `hooks` on its output.
    """
    # If there are no hooks, just use exec so there's no performance hit.
    if not input_hooks and not output_hooks:
        os.execlp(argv[0], *argv)

    parent_fd, child_fd = pty.openpty()

    try:
        original_tty_attr = tty.tcgetattr(STDIN_FILENO)
        tty.setraw(STDIN_FILENO)
    except tty.error:
        original_tty_attr = None

    try:
        # When no signal handler is set for SIGCHLD, select.select will not be
        # interrupted when the subprocess terminates resulting in a deadlock,
        # so a dummy handler is installed to prevent this.
        old_sigchld_handler = signal.getsignal(signal.SIGCHLD)
        if not old_sigchld_handler:
            signal.signal(signal.SIGCHLD, lambda *args: None)

        old_sigwinch_handler = None
        process = subprocess.Popen(argv, close_fds=True, preexec_fn=os.setsid,
          stdin=child_fd, stdout=child_fd, stderr=child_fd)
        os.close(child_fd)

        if sys.stdin.isatty():
            def sigwinch_handler(signal_handled, _):
                """
                Propagate terminal size to child then send SIGWINCH to make it
                aware of the change.
                """
                copy_winsize(parent_fd)
                process.send_signal(signal_handled)

            old_sigwinch_handler = signal.signal(
                signal.SIGWINCH,
                sigwinch_handler
            )
            copy_winsize(parent_fd)
            process.send_signal(signal.SIGWINCH)

        monitored_files = [parent_fd, STDIN_FILENO]
        while monitored_files:
            # Wait until the child process has produced some data or the user
            # has typed something.
            try:
                read_ready = select.select(monitored_files, [], [])[0]
            except EnvironmentError as exc:
                if exc.errno != errno.EINTR:
                    raise
                elif process.poll() is not None:
                    break
                else:
                    continue
            except select.error as exc:
                exc_errno, _ = exc
                if exc_errno != errno.EINTR:
                    raise
                elif process.poll() is not None:
                    break
                else:
                    continue

            if parent_fd in read_ready:
                # If the parent_fd is in the ready state, there's data
                # available from the child process or the process has
                # terminated.
                try:
                    data = read(parent_fd, maxread)
                except EnvironmentError as exc:
                    # An EIO is expected when the process terminates, at least
                    # on Linux. On Mac OS X (and maybe other BSD-based
                    # systems), an EOF signals that the process has terminated.
                    if exc.errno != errno.EIO:
                        raise
                    break

                if data:
                    # Once the data has been read, pass it and the file
                    # descriptor used to communicate with the subprocess to the
                    # hook.
                    for hook in output_hooks:
                        new_data = hook(data, parent_fd)
                        if new_data is not None:
                            if not new_data:
                                break
                            data = new_data

                    write(STDOUT_FILENO, data)

                else:
                    # Stop all processing after EOF from subprocess.
                    break

            if STDIN_FILENO in read_ready:
                # If stdin is in a ready state, either EOF has been reached or
                # there is input from the user that needs to be read then fed
                # into the subprocess.
                user_input = os.read(STDIN_FILENO, maxread)

                if user_input:
                    for hook in input_hooks:
                        new_user_input = hook(user_input, parent_fd)
                        if new_user_input is not None:
                            if not new_user_input:
                                break
                            user_input = new_user_input

                    write(parent_fd, user_input)

                else:
                    # Stop monitoring stdin if EOF is encountered.
                    monitored_files.remove(STDIN_FILENO)

    finally:
        if old_sigwinch_handler:
            signal.signal(signal.SIGWINCH, old_sigwinch_handler)
        if original_tty_attr is not None:
            tty.tcsetattr(STDIN_FILENO, tty.TCSAFLUSH, original_tty_attr)
        if old_sigchld_handler:
            signal.signal(signal.SIGCHLD, old_sigchld_handler)
        os.close(parent_fd)

    return process.wait()


if __name__ == "__main__":
    try:
        options, argv = getopt.getopt(sys.argv[1:], "c:h", ["help"])
    except getopt.GetoptError as exc:
        print("Argument parsing error:", exc, file=sys.stderr)
        sys.exit(1)
    else:
        sys.path.append(os.path.dirname(os.path.realpath(__file__)))
        optdict = dict(options)

        if "-h" in optdict or "--help" in optdict:
            print(__doc__.strip())
            sys.exit(0)

        config_file = optdict.get("-c", DEFAULT_CONFIG_PATH)
        try:
            config_module = imp.load_source("config_module", config_file)
        except EnvironmentError as exc:
            if exc.errno == errno.ENOENT:
                print("Configuration file does not exist: %r" % (config_file,),
                  file=sys.stderr)
            else:
                print("Could not load configuration file %r: %s" % (
                  config_file, exc), file=sys.stderr)
            sys.exit(1)

    if not argv:
        argv = [os.environ.get("SHELL", "/bin/sh")]

    input_hooks = config_module.PTY_INPUT_HOOKS
    output_hooks = config_module.PTY_OUTPUT_HOOKS
    sys.exit(main(argv, input_hooks, output_hooks))
