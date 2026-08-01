import datetime
import os

PASSWORD_PATH = 'password.txt'


class Password:
    def __init__(self, password: str, add_date: str = str(datetime.datetime.now().date()), hit_count: int = 0,
                 last_hit_date: str = ''):
        self.password = password  # 密码内容
        self.add_date = add_date  # 添加日期
        self.hit_count = hit_count  # 命中次数
        self.last_hit_date = last_hit_date  # 最后一次命中日期

    def hit(self):
        """记录密码命中"""
        self.hit_count += 1
        self.last_hit_date = str(datetime.datetime.now().date())


def sort_passwords(passwords: list, last_hit_weight: float) -> list:
    """
    按命中次数和最后命中日期排序密码列表
    
    Args:
        passwords: Password对象列表
        last_hit_weight: 最后命中日期权重
        
    Returns:
        排序后的Password列表
    """
    now = datetime.datetime.now().date()

    def get_score(pwd: Password) -> float:
        # 基础分为命中次数
        score = pwd.hit_count

        # 如果有最后命中日期,计算距今天数并加权
        if pwd.last_hit_date:
            str_hit = datetime.datetime.strptime(pwd.last_hit_date, "%Y-%m-%d").date()
            days = (now - str_hit).days
            score /= 1 + days * last_hit_weight

        return score

    # 按得分从高到低排序
    return sorted(passwords, key=get_score, reverse=True)


def read_password(path: str = PASSWORD_PATH):
    tmp = []
    if not os.path.isfile(path):
        return tmp
    with open(path, "r", encoding='utf-8') as file:
        for line in file.readlines():
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
