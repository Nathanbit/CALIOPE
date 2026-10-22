"""Aritmética acotada para la herramienta `calc` de los agentes.

Sustituye a `eval`: la entrada la escribe un modelo, así que además de impedir
código arbitrario hay que impedir expresiones que agoten CPU o memoria
(`9^9^9`). Solo biblioteca estándar; sin dependencias del resto de themis.
"""
from __future__ import annotations

import ast
import math
import operator
import re

MAX_LEN = 200
MAX_DEPTH = 120
MAX_EXPONENT = 1_000
MAX_INT_BITS = 4_096

_ALLOWED_CHARS = re.compile(r"[\d\s+\-*/().,%^e]+")
_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalcError(ValueError):
    """Expresión rechazada o no evaluable."""


def _check_size(value: float | int) -> float | int:
    if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
        raise CalcError("resultado demasiado grande")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalcError("resultado no finito")
    return value


def _eval(node: ast.AST, depth: int) -> float | int:
    if depth > MAX_DEPTH:
        raise CalcError("expresión demasiado anidada")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _check_size(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left = _eval(node.left, depth + 1)
        right = _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise CalcError(f"exponente fuera de rango (|exp| ≤ {MAX_EXPONENT})")
            if isinstance(left, int) and isinstance(right, int) and right > 0 \
                    and left.bit_length() * right > MAX_INT_BITS:
                raise CalcError("resultado demasiado grande")
        try:
            return _check_size(_BIN[type(node.op)](left, right))
        except (ZeroDivisionError, OverflowError) as exc:
            raise CalcError(str(exc)) from None
    raise CalcError("operación no permitida")


def evaluate(expression: str) -> float | int:
    """Evalúa `expression` (+ - * / // % ^ y paréntesis) o lanza CalcError."""
    if len(expression) > MAX_LEN or not _ALLOWED_CHARS.fullmatch(expression):
        raise CalcError("expresión no permitida")
    try:
        tree = ast.parse(expression.replace("^", "**").replace(",", ""), mode="eval")
    except SyntaxError:
        raise CalcError("sintaxis inválida") from None
    return _eval(tree, 0)
