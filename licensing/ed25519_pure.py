"""تحقّق Ed25519 ببايثون خالصة — احتياطٌ لا يحتاج أي مكتبة.

سبب وجوده: على جهاز معلّمٍ نظيف رفض البرنامج كود التفعيل برسالة
«حزمة cryptography غير مثبَّتة … ثبّتها عبر pip install cryptography».
البرنامج حزمةٌ مجمَّدة، والمستخدم لا يعرف ما pip ولا يجوز أن يُطلب منه.
مكتبة ``cryptography`` امتدادٌ مُترجَم (‏Rust)، وفشل تحميله داخل الحزمة —
ملف DLL ناقص أو استيرادٌ لم يلتقطه المُجمِّع — يُسقط التفعيل كلّه.

التحقّق وحده (لا التوقيع) يُكتب في سبعين سطرًا من RFC 8032 مباشرةً،
ويستغرق أجزاءً من الثانية مرّةً عند التفعيل وعند كل تجديد. فصار التحقّق
لا يعتمد على شيءٍ خارج المكتبة القياسية حين تغيب ``cryptography``.

**التوقيع ليس هنا عمدًا.** المفتاح الخاص عند المالك وحده، وأدوات
إصداره (``tools/make_license.py``) تعمل على جهاز المطوّر بـ cryptography.
وتنفيذٌ بايثوني للتوقيع لا يكون ثابت الزمن، فلا يصلح للسرّ أصلًا.
والتحقّق يعمل على مُدخلات عامّة (المفتاح العام والتوقيع والحمولة)
فلا سرّ فيه يُسرَّب بالتوقيت.

المرجع: RFC 8032 §5.1.7 (Verify) و§6 (Ed25519 in Python).
"""
from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a, b):
    # إحداثيات إسقاطية ممتدّة (X, Y, Z, T) — RFC 8032 §5.1.4
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = 2 * t1 * t2 * _D % _P
    Dv = 2 * z1 * z2 % _P
    E, F, G, H = B - A, Dv - C, Dv + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(scalar: int, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a, b) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * _inv(5) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """``True`` إن كان التوقيع صالحًا لهذه الرسالة بهذا المفتاح العام."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    point_a = _decompress(public_key)
    if point_a is None:
        return False
    r_bytes = signature[:32]
    point_r = _decompress(r_bytes)
    if point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(r_bytes + public_key + message).digest(),
                       "little") % _L
    return _equal(_mul(s, _G), _add(point_r, _mul(h, point_a)))
