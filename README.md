PTYHooks
========

PTYHooks is used to automatically perform actions in response to output
generated by a subprocess. It has been tested with Python 2.6, 2.7 and 3.5 on
Linux and Python 2.7 on Mac OS X. It is similar to [Tck/Tk Expect][tcl-expect]
and [Python Pexpect][pexpect] but was created to augment interactive shells
with automation rather than automate entire processes end-to-end (though that
is not to say total automation with PTYHooks is impossible). Potential uses
include notifying the user when certain text appears on the screen, custom
hotkey macros and modifying the output of a program before it is displayed.

  [tcl-expect]: http://www.tcl.tk/man/expect5.31/expect.1.html "Tck/Tk Expect Manual"
  [pexpect]: http://pexpect.readthedocs.io/en/stable/ "Python Pexpect Documentation"

**Usage:** `ptyhooks [-c CONFIG_MODULE] [-h] [--help] [COMMAND [ARGUMENTS...]]`

License
-------

Apache 2.0; refer to the `LICENSE` file in the root of this repository and the
notice found at the top of each source file.

Make Targets
------------

If `make(1)` is installed, running `make install` will attempt to symlink the
ptyhooks script into `$HOME/bin`. The PTYHooks test suite can be launched by
running `make test`.

Configuration
-------------

PTYHooks is configured by creating a Python module that exposes attributes
named `PTY_INPUT_HOOKS` and `PTY_OUTPUT_HOOKS`. The variables are lists that
contain functions that will be executed when the user provides input or the
subprocess produces output. The functions must accept two positional arguments,
the first of which is the data (as bytes) to processed by the hook, and the
second is the file descriptor for bidirectional communication with the
subprocess; and an arbitrary number of keyword arguments (i.e. `**kwargs`). The
keyword arguments make the hooks to be forward compatible -- in the future,
PTYHooks may choose to pass additional values as keyword arguments, but the
data and file descriptor will always be included. This is a prototypical hook
function:

    def hook(data, subprocess_fd, **kwargs):
        #
        # Do stuff with data here.
        #

The hooks have the ability to manipulate data by returning a value that is not
`None` (`... is not None` in the Python source; return an empty string of bytes
to discard input). Any subsequent hooks will operate on the manipulated data.
Output hooks have priority over user input; if the subprocess generates output
at the same time there is user input available, all output hooks will be
processed before reading the input as shown in this diagram:

    Subprocess Output -> Output Hooks -> PTYHooks Output    |
                                                            |
    User Input -> Input Hooks -> Subprocess Input           V

By default, PTYHooks will attempt to read the configuration file from
`$HOME/.ptyhooks.py`, but this path can be overridden using the "-c" option as
described below.

The PTYHooks script can be imported by configuration modules as `ptyhooks`. The
module exposes wrapped versions of `os.write` and `os.read` as `ptyhooks.write`
and `ptyhooks.read`, respectively. The `ptyhooks.write` function handles
partial writes to the underlying file descriptor so the caller does not need
to. On versions of Python lacking an implementation of [PEP 475][pep-475],
`ptyhooks.read` and `ptyhooks.write` functions handle automatically retrying
the underlying syscalls that fail with [EINTR][error-numbers].

  [pep-475]: https://www.python.org/dev/peps/pep-0475/ "PEP 475"
  [error-numbers]: http://man7.org/linux/man-pages/man0/errno.h.0p.html "System Error Numbers"

### Example Hooks ###

This first example is an output hook. It will sound a bell whenever a prompt
appears. It could be useful for being alerted when a command has finished or
requires user input to proceed:

    def alert_on_prompt(data, subprocess_fd, **kwargs):
        if data.endswith((b": ", b"$ ", b"# ", b"> ", b"? ")):
            sys.stdout.write("\a")
            sys.stdout.flush()

This next example is an input hook that performs [ROT13][rot13] on text the
user enters. It breaks various terminal escape sequences as a result of its
indiscriminate manipulation:

    def rot_13(data, subprocess_fd, **kwargs):
        # Using latin1 to avoid complications caused by data that contains
        # invalid UTF-8 sequences.
        return codecs.encode(data.decode("latin1"), "rot13").encode("latin1")

Refer to `./src/example-ptyhooks-config.py` to see a complete configuration
module.

  [rot13]: https://en.wikipedia.org/wiki/ROT13 "ROT13"

Execution
---------

To wrap a command using PTYHooks, append the command name and arguments used by
the command to `ptyhooks`. If there is no command explicitly given to PTYHooks,
it will launch the application defined in the `SHELL` environment variable and
defaults to `/bin/sh` when that is unset.

Options
-------

Argument parsing is handled using traditional `getopt(3)` instead of GNU
`getopt(3)`. If PTYHooks used GNU `getopt(3)`, `ptyhooks bash -l` would not
work because PTYHooks would try to parse "-l" as though it were intended for
itself instead of Bash.

### -c CONFIG_FILE ###

Load specified file as the configuration module. When unspecified, PTYHooks
will try to use `$HOME/.ptyhooks.py` as the configuration module.

### -h, --help ###

Show documentation and exit.