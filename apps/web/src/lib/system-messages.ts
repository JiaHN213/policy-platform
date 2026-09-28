const systemMessages: Record<string, string> = {
  DOCUMENT_ROLE_NORMALIZED:
    "AI 返回的文档角色不在当前配置范围内，系统已归为“其他”，不影响原文保存。",
  FORMAL_OPPORTUNITY_WITHOUT_EVIDENCE_SKIPPED:
    "AI 提议了正式政策机会，但没有提供可在当前正文中逐字核对的直接依据。系统没有创建该机会，并保留为支持线索或待后续补充附件。",
  RESULT_DOCUMENT_OPPORTUNITY_NORMALIZED:
    "该文件属于公示、正式名单或资金下达等执行结果，系统未把已经发生的结果误建为新的当前政策机会。",
  INVALID_REVIEW_QUOTE_SKIPPED:
    "AI 返回的部分审核依据无法在政策标题或正文中逐字找到，系统已自动删除这些无效引用，仅保留可以核对的原文证据。",
  VALIDITY_WITHOUT_EVIDENCE_NORMALIZED:
    "AI 判断了政策效力，但没有提供能在原文中核对的效力依据，系统已将该项改为“待核实”，避免无依据发布。",
  VALIDITY_STATUS_NORMALIZED:
    "AI 返回的政策效力状态不在系统允许范围内，系统已改为“待核实”，等待重新识别或人工修正。",
  EXPLICIT_VALIDITY_STATUS_RECOGNIZED:
    "系统已从原文中的文件状态说明识别政策效力，并保存对应原文作为依据。",
  HISTORICAL_TARGET_PERIOD_EXPIRED:
    "文件规定的年度目标、专项任务或执行周期已经结束，系统依据原文中的目标年份将政策效力标记为“已失效”。",
  HISTORICAL_OPPORTUNITY_COMPLETED:
    "文件对应的年度项目或任务周期已经结束，系统将政策机会标记为“已完成”；未明确批次名称时仍保留批次待核实。",
  DRAFT_VALIDITY_NORMALIZED:
    "该文件属于征求意见稿，AI 返回的效力状态与文件类型不一致，系统已改为“待核实”。",
  UNKNOWN_TAXONOMY_SKIPPED:
    "AI 返回了系统分类表以外的业务领域或方向标签，系统已自动删除这些无效标签。",
  BUSINESS_DOMAINS_FROM_RULE_FILTER:
    "AI 未返回有效业务领域，系统已采用采集阶段经原文命中确认的水务环保领域。",
  FORMAL_SOURCE_GRADE_REJECTED:
    "来源域名不是政府网站，系统未采用 AI 给出的正式来源等级，避免把非官方页面作为正式政策依据。",
  GEOGRAPHY_INCOMPLETE:
    "AI 给出的地域层级缺少对应省份或城市，系统已将地域改为待核实。",
  GEOGRAPHY_NORMALIZED:
    "AI 给出的地域层级与省市信息不一致，系统已按正式来源中的地域信息自动修正。",
  INCLUDE_REQUIREMENTS_INCOMPLETE:
    "文件虽被 AI 建议纳入，但业务领域、文件类型、正式来源或地域信息仍不完整，因此暂未自动发布。",
  OPPORTUNITY_START_NORMALIZED:
    "AI 返回的项目开始时间不是有效日期，且原文无法核对，系统已将开始时间留空。",
  OPPORTUNITY_DEADLINE_NORMALIZED:
    "AI 返回的申报截止时间不是有效日期，且原文无法核对，系统已将截止时间留空。",
  OPPORTUNITY_DATES_NORMALIZED:
    "AI 返回的开始时间晚于截止时间，时间前后矛盾，系统已将两项时间留空。",
  ONGOING_DEADLINE_NORMALIZED:
    "该机会被识别为常态化受理，但同时出现固定截止时间，系统已清除冲突的截止时间。",
  INVALID_SUMMARY_QUOTE_SKIPPED:
    "AI 返回的部分摘要引文无法在原文中逐字找到，系统已跳过这些内容，未写入政策摘要。",
  INVALID_KEYWORD_SKIPPED:
    "AI 返回的部分关键词未出现在政策原文中，系统已自动删除这些关键词。",
  INVALID_SYNTHESIS_QUOTE_SKIPPED:
    "AI 综合长文摘要时使用了无法核对的引文，系统已保留此前通过原文校验的分段摘要。",
  BODY_EMPTY_OR_TOO_LARGE:
    "政策正文为空或超过当前模型可处理范围，系统无法完成摘要与审核。请先检查正文和附件解析结果。",
  CONTEXT_TOO_LARGE:
    "送入模型的政策内容超过单次处理上限。请检查正文是否重复，或先拆分、精简异常附件内容。",
  INVALID_SUMMARY_QUOTE:
    "AI 返回的摘要依据均无法在政策原文中逐字找到。系统为防止生成错误政策事实，已停止本次审核。",
  INVALID_KEYWORD:
    "AI 返回的关键词无法在政策原文中核对，系统没有保存这些关键词。",
  INVALID_RELATION_EVIDENCE:
    "AI 建议的政策关系缺少可在原文中核对的引用依据，系统没有建立该关系。重新审核后会再次尝试。",
  INVALID_REVIEW_QUOTE:
    "AI 返回的审核依据均无法在政策标题或正文中逐字找到。系统为防止无依据发布，已停止本次审核。",
  INVALID_REVIEW_VALUE:
    "AI 返回的分类或效力状态与原文证据不一致，系统没有采用该结果。重新审核后会再次校验。",
  MODEL_INVALID_OUTPUT:
    "AI 连续两次未返回完整且符合格式要求的审核结果。请稍后重新审核；若持续出现，请检查模型配置和上下文长度。",
  SOURCE_CHANGED:
    "AI 审核期间政策正文或版本发生变化。旧审核结果已作废，请对当前版本重新审核。",
  MODEL_TIMEOUT:
    "AI 服务在规定时间内没有返回结果。政策内容已经保留，可稍后重新审核。",
  MODEL_OR_PROCESSING_ERROR:
    "AI 服务返回异常或审核流程未完整执行。政策内容已经保留，请稍后重新审核；若持续失败，请检查模型服务日志。",
  ATTACHMENTS_REQUIRE_REVIEW:
    "一个或多个政策附件未能完整解析，系统已保留正文和附件链接，需要检查附件后再发布。",
  TITLE_OR_ISSUER_REQUIRES_REVIEW:
    "政策标题或发布机关缺少可靠信息，系统无法确认正式来源，请核对官方原文。",
  IMPORT_LEASE_EXPIRED:
    "正文解析任务曾因服务重启或处理时间过长而中断，系统会从队列中自动恢复并重新解析。",
  BODY_STRUCTURE_UNVERIFIED:
    "网页正文结构与预期不一致，系统无法可靠区分标题、正文和页面导航内容。",
  BODY_TOO_SHORT:
    "提取到的正文过短，可能是空页面、跳转页或需要从附件读取正文。",
  PDF_PROCESS_FAILED:
    "PDF 文件读取失败，可能是文件损坏、下载不完整或格式不受支持。",
  PDF_PARSE_TIMEOUT:
    "PDF 解析耗时超过限制，系统已停止本次处理，避免阻塞后续政策。",
  OCR_OR_PAGE_REVIEW_REQUIRED:
    "附件可能是扫描件或页面文字过少，当前无法可靠提取，需要 OCR 或人工核对。",
  ATTACHMENT_PARSER_REQUIRED:
    "附件已经下载，但文件格式暂不支持自动解析，或文件内容与扩展名不一致。系统已保留附件和原始链接，可直接查看原件。",
  ATTACHMENT_PARSE_REQUIRED:
    "历史解析任务未保留具体异常原因。附件和原始链接已保留，重新解析后将记录具体原因。",
  ATTACHMENT_DOWNLOAD_FAILED:
    "附件下载未完成，可能是原网站临时不可用、链接失效或访问受限。系统已保留附件链接，可稍后重新解析。",
  ATTACHMENT_LIMIT_REQUIRES_REVIEW:
    "该政策包含的附件数量超过单次自动处理上限，正文和附件链接均已保留。",
  ATTACHMENT_ARCHIVE_LIMIT:
    "附件压缩包包含的文件过多或解压后内容过大，系统为避免异常占用资源已停止自动解析。",
  ATTACHMENT_TEXT_LIMIT:
    "附件提取出的文字超过当前处理上限，系统已保留原始附件，可查看原件。",
  XLS_PARSE_FAILED:
    "电子表格附件无法正常读取，可能存在文件损坏、加密或格式与扩展名不一致。",
  ATTACHMENT_TEXT_EMPTY:
    "附件下载成功，但没有提取到可用文字，可能是扫描件或空文件。",
  CONTENT_CHANGED_REQUIRES_VERSION_REVIEW:
    "同一官方链接的政策正文已经变化，系统保留了现有版本，需要按新版本重新解析和审核。",
  NANNING_ACCESS_RESTRICTED:
    "南宁政策网站拒绝了当前访问。系统已停止请求并进入冷却期，之后会从断点自动继续。",
  NANNING_RATE_LIMITED:
    "南宁政策网站要求降低访问频率。系统已暂停请求，冷却结束后会从断点自动继续。",
  NANNING_COOLDOWN_ACTIVE:
    "来源仍处于访问保护冷却期，系统没有继续发送请求，断点和待处理链接均已保留。",
  NON_PUBLIC_ADDRESS:
    "域名被解析到非公网地址，通常由 VPN 的虚拟地址模式造成。请检查直连和 DNS 设置后重试。",
  VPN_FAKE_IP_PROXY_REQUIRED:
    "检测到 VPN 虚拟地址，但系统没有可用代理通道。关闭 VPN 或恢复本地代理后即可继续。",
  VPN_PROXY_UNAVAILABLE:
    "配置的本地 VPN 代理当前无法连接。系统已保留采集断点，代理恢复后可以继续。",
  NANNING_PAGE_STRUCTURE_OR_DATE_MISMATCH:
    "来源页面结构或日期格式发生变化，系统为避免错误登记已停止解析，需要更新采集规则。",
};

const internalCode = /^[A-Z][A-Z0-9_]+$/;
const genericSystemMessage =
  "系统发现一项处理异常，相关数据已保留。请重新处理；若问题持续出现，请查看服务日志。";
const internalValueLabels: Record<string, string> = {
  other: "其他政策支持",
  unverified: "待核实",
  include: "纳入",
  exclude: "排除",
  needs_review: "待核实",
  expired: "已失效",
  repealed: "已废止",
  replaced: "已被替代或修订",
  effective: "现行有效",
  completed: "已完成",
  closed: "已截止",
  ongoing: "常态化受理",
  not_started: "尚未开始",
  queued: "等待处理",
  running: "处理中",
  succeeded: "处理完成",
  failed: "处理未完成",
};

export function explainSystemMessage(
  value?: string,
  fallback = genericSystemMessage,
) {
  if (!value) return fallback;
  if (systemMessages[value]) return systemMessages[value];
  if (value.startsWith("MODEL_HTTP_")) {
    return "AI 服务返回了请求错误，系统未采用本次结果。请稍后重新审核；若持续失败，请检查模型地址、密钥、额度和服务状态。";
  }
  if (!internalCode.test(value)) return value;
  return fallback &&
    !internalCode.test(fallback) &&
    /[\u3400-\u9fff]/.test(fallback)
    ? fallback
    : genericSystemMessage;
}

export function explainSystemText(value?: string) {
  if (!value) return "";
  return value
    .replace(/错误代码[：:]\s*([A-Z][A-Z0-9_]+)[。.]?/g, (_, code: string) =>
      explainSystemMessage(code),
    )
    .replace(/\b[A-Z][A-Z0-9_]{3,}\b/g, (code) => explainSystemMessage(code))
    .replace(
      /\b(other|unverified|include|exclude|needs_review|expired|repealed|replaced|effective|completed|closed|ongoing|not_started|queued|running|succeeded|failed)\b/g,
      (token) => internalValueLabels[token] || token,
    );
}
