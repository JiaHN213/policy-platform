"""Safe, user-readable extraction failures; no raw response bodies or credentials."""

import logging
import zipfile
from xml.etree.ElementTree import ParseError

import httpx

from .gov_library import SourceUnavailable

logger = logging.getLogger(__name__)


def attachment_failure(exc, stage):
    if isinstance(exc, SourceUnavailable):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "附件下载超时，原网站未及时响应，可稍后重试。"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return "附件链接已失效，原网站返回文件不存在，请核对原文中的附件地址。"
        if code in {403, 429}:
            return "原网站限制附件访问，请等待来源冷却后重试。"
        return f"附件服务器返回异常状态（{code}），可稍后重试。"
    if isinstance(exc, httpx.RequestError):
        return "无法连接附件服务器，请检查网络、代理或来源可用性后重试。"
    if isinstance(exc, zipfile.BadZipFile):
        return "附件压缩结构损坏或下载不完整，无法读取内部文档，请重新下载。"
    if isinstance(exc, ParseError):
        return "附件内部文档结构损坏，无法读取正文，请核对或重新下载原件。"
    if isinstance(exc, PermissionError):
        return "服务无法读写附件存储目录，请检查运行账号的文件权限。"
    if isinstance(exc, KeyError):
        return "附件缺少必需的正文或工作表结构，可能损坏或格式不受支持。"
    logger.error("Attachment %s failed (%s)", stage, type(exc).__name__)
    return f"附件{ '下载或保存' if stage == 'download' else '解析' }发生程序异常，原始链接已保留，请检查服务日志。"
