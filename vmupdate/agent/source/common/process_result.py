from copy import deepcopy


class ProcessResult:
    def __init__(self, code: int = 0, out: str = "", err: str = ""):
        self.code: int = code
        self.out: str = out
        self.err: str = err

    @classmethod
    def process_communicate(cls, proc):
        out, err = proc.communicate()
        code = proc.returncode
        return cls(code, out.decode(), err.decode())

    def __add__(self, other):
        new = deepcopy(self)
        new += other
        return new

    def __iadd__(self, other):
        if not isinstance(other, ProcessResult):
            raise TypeError("unsupported operand type(s) for +:"
                            f"'{self.__class__.__name__}' and "
                            f"'{other.__class__.__name__}'")
        self.code = max(self.code, other.code)
        self.out += other.out
        self.err += other.err
        return self

    def error_from_messages(self):
        out_lines = (self.out + self.err).splitlines()
        if any(line.lower().startswith("err") for line in out_lines):
            self.code = 1
