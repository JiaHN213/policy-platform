from django.db import models


class ValidityStatus(models.TextChoices):
    CONSULTATION = "consultation", "征求意见"
    NOT_EFFECTIVE = "not_effective", "尚未生效"
    EFFECTIVE = "effective", "现行有效"
    EXPIRED = "expired", "已失效"
    REPEALED = "repealed", "已废止"
    REPLACED = "replaced", "已被替代/已修订"
    UNVERIFIED = "unverified", "待核实"


class OpportunityCategory(models.TextChoices):
    FISCAL = "fiscal", "财政资金支持"
    TAX = "tax", "税费优惠"
    FINANCE = "finance", "融资支持"
    PILOT = "pilot", "项目与试点"
    HONOR = "honor", "荣誉与认定"
    QUALIFICATION = "qualification", "资质与目录"
    MARKET = "market", "市场与推广支持"
    OTHER = "other", "其他政策支持"


class OpportunityStatus(models.TextChoices):
    NOT_STARTED = "not_started", "未开始"
    OPEN = "open", "申报中"
    CLOSED = "closed", "已截止"
    ONGOING = "ongoing", "长期有效/常态化受理"
    SUSPENDED = "suspended", "暂停/中止"
    PUBLICITY = "publicity", "结果公示中"
    COMPLETED = "completed", "已完成"
    UNVERIFIED = "unverified", "待核实"


class OpportunityLevel(models.TextChoices):
    NONE = "NONE", "没有政策机会"
    SUPPORT_SIGNAL = "SUPPORT_SIGNAL", "政策支持方向"
    FORMAL = "FORMAL_OPPORTUNITY", "正式政策机会"


class DocumentRole(models.TextChoices):
    POLICY_BASIS = "POLICY_BASIS", "政策依据"
    APPLICATION_NOTICE = "APPLICATION_NOTICE", "申报通知"
    APPLICATION_GUIDE = "APPLICATION_GUIDE", "申报指南"
    SUPPLEMENT_NOTICE = "SUPPLEMENT_NOTICE", "补充通知"
    EXTENSION_NOTICE = "EXTENSION_NOTICE", "延期通知"
    CONSULTATION_DRAFT = "CONSULTATION_DRAFT", "征求意见稿"
    PUBLICITY_RESULT = "PUBLICITY_RESULT", "结果公示"
    FINAL_RESULT = "FINAL_RESULT", "正式结果"
    FUND_ALLOCATION = "FUND_ALLOCATION", "资金下达"
    OFFICIAL_INTERPRETATION = "OFFICIAL_INTERPRETATION", "官方解读"
    OTHER = "OTHER", "其他"


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "待核验"
    VERIFIED = "verified", "已核验"
    REJECTED = "rejected", "核验不通过"


class RelationKind(models.TextChoices):
    SUPERIOR = "superior", "上位"
    IMPLEMENTS = "implements", "实施"
    SUPPORTS = "supports", "配套"
    APPLICATION = "application", "申报通知"
    SUPPLEMENTS = "supplements", "补充"
    EXTENDS = "extends", "延期"
    INTERPRETS = "interprets", "解读"
    FINALIZES = "finalizes", "征求→正式"
    REVISES = "revises", "修订"
    REPLACES = "replaces", "替代"
    REPEALS = "repeals", "废止"
    PUBLICIZES = "publicizes", "公示"
    LISTS = "lists", "正式名单"
    ALLOCATES = "allocates", "资金下达"
    APPROVES = "approves", "项目批复"
    ACCEPTS = "accepts", "验收结果"
