"""M5-2 账号体系：密码单向哈希工具。

凭据安全红线（M5-1 合同 / credential_security_redlines）：
- 密码**只**以单向哈希 + 每用户随机盐存储，绝不存明文、绝不可逆。
- 本模块只产出 / 校验哈希字符串；明文密码只在函数入参里短暂存在，
  绝不写入任何持久层、日志、报告、JSON 或测试快照。
- 哈希算法可替换（生产可换 bcrypt / argon2）：默认用标准库
  ``hashlib.pbkdf2_hmac``（SHA-256），零额外依赖、可在无网络 VM 内单测。

存储格式（单字段，自描述，便于将来平滑迁移算法）：

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

只有这一串会进 ``password_hash`` 列；明文永不落库。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# 默认算法标识与迭代次数。迭代次数写进哈希串本身，校验时按串内值执行，
# 因此将来调高迭代数不会让旧哈希失效。
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_DEFAULT_ITERATIONS = 240_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_password(plaintext: str, *, iterations: int = PBKDF2_DEFAULT_ITERATIONS) -> str:
    """把明文密码转成不可逆的存储串。明文不在返回值里、不落任何持久层。"""
    if not isinstance(plaintext, str) or plaintext == "":
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", plaintext.encode("utf-8"), salt, iterations, dklen=_HASH_BYTES
    )
    return f"{PBKDF2_ALGORITHM}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """常量时间校验明文与存储哈希是否匹配。失败一律返回 False，不抛敏感信息。"""
    if not isinstance(plaintext, str) or not isinstance(stored_hash, str):
        return False
    try:
        algorithm, iterations_str, salt_hex, expected_hex = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", plaintext.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(derived, expected)


def is_hashed(value: str) -> bool:
    """判断一个串是否已是本模块产出的哈希格式（用于防止明文误入库的断言）。"""
    if not isinstance(value, str):
        return False
    parts = value.split("$")
    return len(parts) == 4 and parts[0] == PBKDF2_ALGORITHM
