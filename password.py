from __future__ import annotations

import datetime
import os

PASSWORD_PATH = 'password.txt'


class Password:
    def __init__(self, password: str, add_date: str | None = None, hit_count: int = 0,
                 last_hit_date: str = ''):
        self.password = password  # 密码内容
        self.add_date = (
            str(datetime.datetime.now().date()) if add_date is None else add_date
        )  # 添加日期
        self.hit_count = hit_count  # 命中次数
        self.last_hit_date = last_hit_date  # 最后一次命中日期

    def hit(self):
        """记录密码命中"""
        self.hit_count += 1
        self.last_hit_date = str(datetime.datetime.now().date())


def sort_passwords(passwords: list, last_hit_weight: float = 0.5) -> list:
    """
    按添加日期和命中次数排序密码列表。

    最近添加的密码优先；添加日期相同时，命中次数更高的优先。
    
    Args:
        passwords: Password对象列表
        last_hit_weight: 为兼容旧调用保留，不再参与排序
        
    Returns:
        排序后的Password列表
    """
    def get_add_date(pwd: Password) -> datetime.date:
        try:
            return datetime.datetime.strptime(pwd.add_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            # 旧密码库中的非法日期不应被当作新密码优先尝试。
            return datetime.date.min

    return sorted(
        passwords,
        key=lambda pwd: (get_add_date(pwd), pwd.hit_count),
        reverse=True,
    )


def prioritize_latest_hits(passwords: list[Password]) -> list[Password]:
    """将最近一次命中的密码放到快速通道，其余保持原排序。"""
    ordered = sort_passwords(passwords)

    def get_last_hit_date(pwd: Password) -> datetime.date | None:
        try:
            return datetime.datetime.strptime(pwd.last_hit_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    dated = [(pwd, get_last_hit_date(pwd)) for pwd in ordered]
    valid_dates = [date for _pwd, date in dated if date is not None]
    if not valid_dates:
        return ordered

    latest = max(valid_dates)
    fast_lane = [pwd for pwd, date in dated if date == latest]
    fast_lane.sort(key=lambda pwd: pwd.hit_count, reverse=True)
    return fast_lane + [pwd for pwd in ordered if pwd not in fast_lane]


def _read_password_text(path: str) -> str:
    """读取密码库文本；非 UTF-8 文件不再让整个程序启动失败。

    用户常用记事本以 ANSI/GBK 另存 password.txt（密码含日文/中文时尤其常见），
    固定 encoding='utf-8' 会抛 UnicodeDecodeError；该调用点在 main.py 的启动异常
    处理里，会让程序每次启动都直接失败，用户只能手工找到并修复该文件。
    """
    with open(path, 'rb') as file:
        raw = file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'cp932', 'gbk'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 全部编码都失败时仍以替换字符继续，保证程序能启动。
    return raw.decode('utf-8', errors='replace')


def read_password(path: str = PASSWORD_PATH):
    tmp = []
    if not os.path.isfile(path):
        return tmp
    for line in _read_password_text(path).splitlines():
        line = line.strip('\n').strip('\r')  # 去掉换行符
        # 跳过空行 / 仅含空白或制表符的行，避免脏行导致整个程序启动崩溃
        if not line.strip():
            continue
        str_pw = line.split("\t")
        pw_text = str_pw[0]
        if not pw_text:
            # 密码本体为空则忽略该行（例如误存了 "\t..." 这样的脏数据）
            continue
        add_date = str_pw[1] if len(str_pw) > 1 and str_pw[1] else str(datetime.datetime.now().date())
        try:
            hit_count = int(str_pw[2]) if len(str_pw) > 2 and str_pw[2] else 0
        except ValueError:
            hit_count = 0
        last_hit_date = str_pw[3] if len(str_pw) > 3 else ''
        tmp.append(Password(pw_text, add_date, hit_count, last_hit_date))
    return tmp


def write_password(passwords: list[Password], path: str = PASSWORD_PATH):
    """写入密码库；先写临时文件再原子替换，避免写入中断损坏文件。"""
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as file:
        for item in passwords:
            str_pw = (
                f'{item.password}\t{item.add_date}\t{item.hit_count}\t{item.last_hit_date}'
            )
            file.write(str_pw + '\n')
    os.replace(tmp_path, path)


def get_str_passwords(passwords: list[Password]):
    str_passwords = []
    for password in passwords:
        str_passwords.append(password.password)
    return str_passwords


def hit_password(passwords: list[Password], str_password: str):
    for password in passwords:
        if password.password == str_password:
            password.hit()
            return True
    return False
