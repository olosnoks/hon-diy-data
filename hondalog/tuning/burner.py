"""Burn a ROM to an SST27SF512 with a TL866II+ or T48, through ``minipro``.

``minipro`` is the open-source command-line tool for these programmers.
This module only builds its command lines, runs them, and checks the result:

    erase (-E)  ->  blank check (-b)  ->  write (-w, with its own verify)
    ->  verify (-m)  ->  read back (-r) and compare byte for byte

The option letters are from the minipro man page. Two things are *not*
confirmed here because no programmer was attached: the exact device name
minipro wants for this chip (it is looked up with ``minipro -L`` at run
time), and the wording of minipro's messages (so success is judged by exit
status and by the read-back compare, never by parsing text).

``dry_run=True`` runs nothing and returns the commands that would run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .rom import CHIP_27SF512_SIZE, BurnImage, sha256_hex

DEFAULT_DEVICE = "SST27SF512"
INSTALL_HINT = ("minipro is not installed. On Debian try 'sudo apt install minipro', "
                "or build it from https://gitlab.com/DavidGriffith/minipro")

Runner = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]
Progress = Callable[[str, int, int], None]


class BurnerError(Exception):
    pass


@dataclass
class Step:
    name: str                    # erase, blank_check, write, verify, read_back, compare
    command: list[str]
    returncode: int | None = None
    output: str = ""
    ok: bool = False
    skipped: bool = False


@dataclass
class BurnResult:
    ok: bool
    dry_run: bool
    device: str
    image_sha256: str
    rom_sha256: str = ""
    readback_sha256: str = ""
    started: str = ""
    steps: list[Step] = field(default_factory=list)
    message: str = ""

    def commands(self) -> list[str]:
        """The command lines, quoted for reading or pasting into a shell."""
        import shlex
        return [shlex.join(s.command) for s in self.steps if s.command]

    def to_record(self) -> dict:
        record = asdict(self)
        for step in record["steps"]:
            step["output"] = step["output"][-2000:]
        return record


def find_minipro(executable: str | None = None) -> str | None:
    """Path of the minipro binary, or None."""
    if executable:
        return executable if (os.path.isfile(executable)
                              and os.access(executable, os.X_OK)) else shutil.which(executable)
    return os.environ.get("HONDALOG_MINIPRO") or shutil.which("minipro")


def _run(command: Sequence[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(command), capture_output=True, text=True,
                          timeout=timeout, errors="replace")


class Minipro:
    """A thin, testable wrapper around the minipro command line."""

    def __init__(self, executable: str | None = None, device: str = DEFAULT_DEVICE,
                 *, runner: Runner | None = None, timeout: float = 180.0,
                 ignore_id_error: bool = False, extra_args: Sequence[str] = ()):
        self.executable = find_minipro(executable)
        self.requested = executable or "minipro"
        self.device = device
        self.timeout = timeout
        self.ignore_id_error = ignore_id_error
        self.extra_args = list(extra_args)
        self._runner: Runner = runner or _run
        self._custom_runner = runner is not None

    # -- plumbing --

    @property
    def available(self) -> bool:
        return self.executable is not None or self._custom_runner

    def _exe(self) -> str:
        if self.executable:
            return self.executable
        if self._custom_runner:
            return self.requested
        raise BurnerError(INSTALL_HINT)

    def run(self, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        command = [self._exe(), *args]
        try:
            return self._runner(command, self.timeout)
        except FileNotFoundError as exc:
            raise BurnerError(INSTALL_HINT) from exc
        except subprocess.TimeoutExpired as exc:
            raise BurnerError(
                f"minipro did not finish within {self.timeout:.0f} s: "
                f"{' '.join(command)}") from exc
        except OSError as exc:
            raise BurnerError(f"cannot run minipro: {exc}") from exc

    @staticmethod
    def _text(proc: "subprocess.CompletedProcess[str]") -> str:
        return ((proc.stdout or "") + (proc.stderr or "")).strip()

    # -- queries --

    def version(self) -> str:
        """First line of ``minipro -V``."""
        text = self._text(self.run(["-V"]))
        return text.splitlines()[0].strip() if text else "(no version text)"

    def programmer(self) -> str:
        """What ``minipro -k`` says about the attached programmer."""
        proc = self.run(["-k"])
        text = self._text(proc)
        if proc.returncode != 0:
            raise BurnerError("no programmer found. Check the USB cable and that you "
                              "may use the device (udev rules / plugdev group).\n" + text)
        return text.splitlines()[-1].strip() if text else "programmer present"

    def matching_devices(self, search: str | None = None) -> list[str]:
        """Device names minipro knows that contain ``search``."""
        proc = self.run(["-L", search or self.device])
        names = []
        for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
            line = line.strip()
            if line and (search or self.device).upper() in line.upper() and " " not in line:
                names.append(line)
        return list(dict.fromkeys(names))

    def resolve_device(self) -> str:
        """The exact device name to pass to ``-p``.

        Prefers an exact match, then the DIP28 package (the HONDA1 kit's
        socket takes the 28-pin DIP chip).
        """
        names = self.matching_devices()
        if not names:
            raise BurnerError(
                f"this minipro does not list a device matching {self.device!r}. "
                "Update minipro, or check 'minipro -L 27SF512'.")
        upper = {n.upper(): n for n in names}
        if self.device.upper() in upper:
            return upper[self.device.upper()]
        for name in names:
            if "DIP28" in name.upper():
                return name
        return names[0]

    # -- command lines --

    def _base(self, device: str) -> list[str]:
        return ["-p", device, *(["-y"] if self.ignore_id_error else []), *self.extra_args]

    def plan(self, image_path: str | Path, readback_path: str | Path, *,
             device: str | None = None, erase: bool = True, blank_check: bool = True,
             verify: bool = True, read_back: bool = True) -> list[Step]:
        """The steps of a burn, as argument lists, without running anything."""
        exe = self.executable or self.requested
        base = [exe, *self._base(device or self.device)]
        steps = []
        if erase:
            steps.append(Step("erase", [*base, "-E"]))
        if blank_check:
            steps.append(Step("blank_check", [*base, "-b"]))
        # After our own erase, tell minipro not to erase again.
        steps.append(Step("write", [*base, "-w", str(image_path), *(["-e"] if erase else [])]))
        if verify:
            steps.append(Step("verify", [*base, "-m", str(image_path)]))
        if read_back:
            steps.append(Step("read_back", [*base, "-r", str(readback_path)]))
            steps.append(Step("compare", []))
        return steps

    # -- operations --

    def read_chip(self, device: str | None = None) -> bytes:
        """Read the whole chip."""
        device = device or self.resolve_device()
        with tempfile.TemporaryDirectory(prefix="hondalog-read-") as tmp:
            out = Path(tmp) / "chip.bin"
            proc = self.run([*self._base(device), "-r", str(out)])
            if proc.returncode != 0 or not out.exists():
                raise BurnerError("reading the chip failed:\n" + self._text(proc))
            return out.read_bytes()

    def burn(self, image: BurnImage | bytes, *, dry_run: bool = False, erase: bool = True,
             blank_check: bool = True, verify: bool = True, read_back: bool = True,
             chip_size: int = CHIP_27SF512_SIZE, progress: Progress | None = None,
             log_path: str | Path | None = None) -> BurnResult:
        """Burn and check. Returns a :class:`BurnResult`; ``ok`` is the verdict.

        Pass the :class:`~hondalog.tuning.rom.BurnImage` from
        ``session.burn_image()`` so the safety checks have already run. Raw
        bytes are accepted for re-burning a known-good file.
        Raises :class:`BurnerError` only for problems before anything is
        written (minipro missing, wrong image size, unknown chip).
        """
        if isinstance(image, BurnImage):
            data, rom_hash = image.data, image.rom_sha256
        else:
            data, rom_hash = bytes(image), ""
        if len(data) != chip_size:
            raise BurnerError(
                f"the image is {len(data)} bytes; the chip holds {chip_size}. "
                "Build it with session.burn_image().")
        started = datetime.now().isoformat(timespec="seconds")
        image_hash = sha256_hex(data)
        if dry_run:
            steps = self.plan("<image.bin>", "<readback.bin>", erase=erase,
                              blank_check=blank_check, verify=verify, read_back=read_back)
            for s in steps:
                s.skipped = True
            return BurnResult(True, True, self.device, image_hash, rom_hash,
                              started=started, steps=steps,
                              message="dry run: nothing was sent to the programmer")
        device = self.resolve_device()
        result = BurnResult(False, False, device, image_hash, rom_hash, started=started)
        with tempfile.TemporaryDirectory(prefix="hondalog-burn-") as tmp:
            image_path = Path(tmp) / "image.bin"
            back_path = Path(tmp) / "readback.bin"
            image_path.write_bytes(data)
            steps = self.plan(image_path, back_path, device=device, erase=erase,
                              blank_check=blank_check, verify=verify, read_back=read_back)
            result.steps = steps
            for i, step in enumerate(steps):
                if progress:
                    progress(step.name, i, len(steps))
                if step.name == "compare":
                    got = back_path.read_bytes() if back_path.exists() else b""
                    result.readback_sha256 = sha256_hex(got)
                    step.ok = got == data
                    step.returncode = 0 if step.ok else 1
                    if not step.ok:
                        bad = sum(a != b for a, b in zip(got, data)) + abs(len(got) - len(data))
                        step.output = f"{bad} bytes differ from the image"
                else:
                    proc = self.run(step.command[1:])
                    step.returncode = proc.returncode
                    step.output = self._text(proc)
                    step.ok = proc.returncode == 0
                if not step.ok:
                    result.message = _failure_text(step)
                    for rest in steps[i + 1:]:
                        rest.skipped = True
                    break
            else:
                result.ok = True
                result.message = ("burned and verified" if (verify or read_back)
                                  else "burned (not verified)")
            if progress:
                progress("done" if result.ok else "failed", len(steps), len(steps))
        if log_path:
            append_burn_log(Path(log_path), result)
        return result


def _failure_text(step: Step) -> str:
    what = {
        "erase": "Erasing the chip failed. Check that the chip is seated with pin 1 "
                 "at the lever end and that it is an SST27SF512.",
        "blank_check": "The chip is not blank after erasing. It may be worn out or "
                       "the wrong type; do not use it.",
        "write": "Writing failed.",
        "verify": "The chip does not match the image after writing. Do not put it "
                  "in the ECU.",
        "read_back": "Reading the chip back failed.",
        "compare": "The read-back differs from the image. Do not put this chip in "
                   "the ECU.",
    }[step.name]
    tail = "\n".join(step.output.splitlines()[-6:])
    return f"{what}\n{tail}".strip()


def append_burn_log(path: Path, result: BurnResult) -> None:
    """One JSON line per burn: time, hashes, device, verdict, command output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_record()) + "\n")


def read_burn_log(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
