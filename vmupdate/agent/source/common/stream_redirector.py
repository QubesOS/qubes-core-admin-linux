# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022  Piotr Bartman <prbartman@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.

import io
import os
import sys
import tempfile
import ctypes

from source.common.process_result import ProcessResult


class StreamRedirector:
    """
    Redirects streams (including both Python and C-level streams).

    The `apt` package write directly to C-level stdout/stderr stream
    so simple capturing `sys.stdout`/`sys.stderr` does not work.
    """
    LIBC = ctypes.CDLL(None)
    C_STDOUT = ctypes.c_void_p.in_dll(LIBC, 'stdout')
    C_STDERR = ctypes.c_void_p.in_dll(LIBC, 'stderr')

    def __init__(self, dest_result: ProcessResult):
        self.dest_result = dest_result
        self.dest_out = io.BytesIO()
        self.dest_err = io.BytesIO()
        self.stdout_file_descriptor = sys.stdout.fileno()
        self.stderr_file_descriptor = sys.stderr.fileno()

        self._stdout_file_descriptor_copy = os.dup(self.stdout_file_descriptor)
        self._stderr_file_descriptor_copy = os.dup(self.stderr_file_descriptor)

        self._temp_stdout = tempfile.TemporaryFile(mode='w+b')
        self._temp_stderr = tempfile.TemporaryFile(mode='w+b')

    def __enter__(self):
        self._redirect_stdout(self._temp_stdout.fileno())
        self._redirect_stderr(self._temp_stderr.fileno())

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._redirect_stdout(self._stdout_file_descriptor_copy)
            self._redirect_stderr(self._stderr_file_descriptor_copy)

            # Redirect the content of temporary files to the stream
            self._temp_stdout.flush()
            self._temp_stderr.flush()
            self._temp_stdout.seek(0, io.SEEK_SET)
            self._temp_stderr.seek(0, io.SEEK_SET)
            self.dest_out.write(self._temp_stdout.read())
            self.dest_err.write(self._temp_stderr.read())
        finally:
            self._temp_stdout.close()
            self._temp_stderr.close()
            os.close(self._stdout_file_descriptor_copy)
            os.close(self._stderr_file_descriptor_copy)

        self.dest_out.flush()
        self.dest_out.seek(0, io.SEEK_SET)
        out = self.dest_out.read().decode()
        self.dest_err.flush()
        self.dest_err.seek(0, io.SEEK_SET)
        err = self.dest_err.read().decode()
        self.dest_result += ProcessResult(0, out, err)

    def _redirect_stdout(self, destination):
        """
        Redirect stdout to the given file descriptor.

        :param destination: file descriptor
        """
        StreamRedirector.LIBC.fflush(StreamRedirector.C_STDOUT)
        sys.stdout.close()  # it flushes stdout and closes the file descriptor
        # Overwrite `sys.stdout` to point destination file descriptor
        os.dup2(destination, self.stdout_file_descriptor)
        sys.stdout = io.TextIOWrapper(
            os.fdopen(self.stdout_file_descriptor, 'wb'))

    def _redirect_stderr(self, destination):
        """
        Redirect stderr to the given file descriptor.

        :param destination: file descriptor
        """
        # Flush the C-level buffer stderr
        StreamRedirector.LIBC.fflush(StreamRedirector.C_STDERR)
        sys.stderr.close()  # it flushes stderr and closes the file descriptor
        # Overwrite `sys.stderr` to point destination file descriptor
        os.dup2(destination, self.stderr_file_descriptor)
        sys.stderr = io.TextIOWrapper(
            os.fdopen(self.stderr_file_descriptor, 'wb'))
