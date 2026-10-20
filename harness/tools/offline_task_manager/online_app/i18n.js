(function scienceVisionI18n(global) {
  'use strict';

  const STORAGE_KEY = 'sciencevision.language';
  const ERROR_PREFIX = 'error.';
  const VIEWER_PREFIX = 'viewer.';
  const DEFAULT_LANGUAGE = 'zh-CN';
  const LANGUAGES = ['zh-CN', 'en'];

  // Text attributes an element may localize. `data-i18n` sets textContent;
  // the rest set the matching attribute.
  const ATTRIBUTE_KEYS = [
    ['data-i18n-placeholder', 'placeholder'],
    ['data-i18n-title', 'title'],
    ['data-i18n-aria-label', 'aria-label'],
    ['data-i18n-value', 'value'],
  ];

  const TABLES = {
    'zh-CN': {
      'lang.switchTo': 'EN',
      'lang.switchTitle': '切换到英文界面',
      'login.documentTitle': 'ScienceVision 登录',
      'login.heading': 'ScienceVision 审核',
      'login.intro': '已分配的 3D 任务、校验反馈、答案与推理过程，集中在一个工作台。',
      'login.facet.viewer.term': '查看器',
      'login.facet.viewer.desc': '按任务定制的 3D 查看器',
      'login.facet.drafts.term': '草稿',
      'login.facet.drafts.desc': '保存在当前浏览器中',
      'login.facet.submission.term': '提交',
      'login.facet.submission.desc': '校验后集中存储',
      'login.secureAccess': '安全访问',
      'login.signInHeading': '登录',
      'login.signInHint': '请使用管理员分配给你的专家账号凭据。',
      'login.usernameLabel': '用户名',
      'login.passwordLabel': '密码',
      'login.submit': '继续',
      'login.activationEyebrow': '账号激活',
      'login.activationHeading': '设置你的密码',
      'login.activationHint': '临时密码无法打开已分配的任务，请设置一个仅你本人知晓的密码。',
      'login.newPasswordLabel': '新密码',
      'login.confirmPasswordLabel': '确认新密码',
      'login.activateSubmit': '激活账号',
      'login.signOut': '退出登录',
      'login.status.signingIn': '正在登录…',
      'login.status.activating': '正在激活账号…',
      'login.error.signInFailed': '登录失败',
      'login.error.activationFailed': '账号激活失败',
      'login.error.sessionLoad': '无法加载密码激活会话',
      'expert.documentTitle': 'ScienceVision 专家审核',
      'expert.brand': 'ScienceVision 专家审核',
      'expert.packageLoading': '正在加载任务包…',
      'expert.focusViewer': '专注查看器',
      'expert.focusViewerTitle': '把整个工作区交给 3D 查看器',
      'expert.showTasks': '显示任务与作答',
      'expert.showTasksTitle': '显示已分配任务与作答区',
      'expert.exportResults': '导出结果',
      'expert.submitResults': '提交当前题',
      'expert.saveDraft': '保存草稿',
      'expert.status.savedServer': '已保存到服务器',
      'expert.signOut': '退出登录',
      'expert.tasksRailLabel': '已分配任务',
      'expert.tasksLabel': '任务',
      'expert.filter.all': '全部',
      'expert.filter.complete': '已完成',
      'expert.filter.incomplete': '未完成',
      'expert.selectTask': '请选择一个任务',
      'expert.validationLabel': '任务校验',
      'expert.validation.uncertain': '不确定',
      'expert.validation.valid': '有效',
      'expert.validation.invalid': '无效',
      'expert.validationFeedbackLabel': '校验反馈',
      'expert.answerLabel': '答案',
      'expert.reasoningLabel': '推理过程',
      'expert.steps.legend': '关键操作',
      'expert.steps.hint': '只记真正改变判断的那几步，并写清它为什么关键；不用记录每一次鼠标移动。',
      'expert.steps.actionLabel': '操作',
      'expert.steps.action.zoomIn': '放大',
      'expert.steps.action.zoomOut': '缩小',
      'expert.steps.action.rotate': '旋转',
      'expert.steps.action.pan': '平移',
      'expert.steps.action.slice': '切换切面',
      'expert.steps.action.measure': '测量',
      'expert.steps.action.isolate': '隔离结构',
      'expert.steps.action.resetView': '复位视角',
      'expert.steps.action.screenshot': '截图取证',
      'expert.steps.action.other': '其他',
      'expert.steps.noteLabel': '为什么关键',
      'expert.steps.notePlaceholder': '例如：放大一次仍看不清配体，于是继续放大到原子层级',
      'expert.steps.add': '记录这一步',
      'expert.steps.remove': '删除',
      'expert.steps.removeTitle': '删除这一步',
      'expert.steps.noteRequired': '请写清这一步为什么关键',
      'expert.steps.recorded': '已记录一步关键操作',
      'expert.steps.removed': '已删除一步关键操作',
      'expert.steps.tooMany': '最多记录 {limit} 步关键操作',
      'expert.attemptLabel': '尝试次数',
      'expert.viewerTitle': 'ScienceVision 3D 查看器',
      'expert.viewerBarTitle': '3D 查看器',
      'expert.unclassified': '未分类',
      'expert.progress': '已完成 {complete} / {total}',
      'expert.status.attemptInvalid': '尝试次数必须大于或等于 1',
      'expert.status.savedLocally': '已保存到本地',
      'expert.status.localSaveFailed': '本地保存失败',
      'expert.status.alreadySubmitted': '已提交，内容无变化',
      'expert.status.submitted': '已提交 {count} 条结果',
      'expert.error.draftSave': '草稿保存失败：{message}',
      'expert.error.draftLoad': '无法加载本地草稿：{message}',
      'expert.error.taskOpenHttp': '无法打开任务（HTTP {status}）',
      'expert.error.taskOpen': '无法打开任务',
      'expert.error.taskOpenInvalid': '打开任务的返回内容无效',
      'expert.error.taskSelect': '任务切换失败：{message}',
      'expert.error.incompleteTasks': '还有 {count} 条任务缺少校验反馈、答案或推理过程。',
      'expert.error.incompleteCurrentTask': '当前题目还缺少校验反馈、答案或推理过程。',
      'expert.error.trajectoryUnavailable': '轨迹采集不可用：{message}',
      'expert.error.submissionFailed': '提交失败',
      'expert.error.manifestLoad': '清单加载失败：HTTP {status}',
      'expert.error.bundleManifest': '无法加载专家任务包清单',
      'expert.error.bootstrap': '无法加载当前任务包',
      'expert.error.tasksLoad': '无法加载已分配任务',
      'expert.error.taskCountMismatch': '任务数量与清单不一致',
      'expert.guide.summary': '操作指南：这一页怎么做',
      'expert.guide.flow': '整体流程：左侧选题 → 在 3D 查看器里观察并截图取证 → 判定题目是否有效 → 写答案与推理过程 → 右上角提交当前题。',
      'expert.guide.s1.title': '01 选题',
      'expert.guide.s1.io': '输入：左侧「任务」列表，可用「未完成」筛选 → 产出：右侧显示题目、说明与对应的 3D 场景',
      'expert.guide.s2.title': '02 观察场景',
      'expert.guide.s2.io': '输入：在 3D 查看器里拖拽旋转、滚轮缩放；右上角「专注查看器」把整个工作区交给查看器 → 产出：看清题目要判断的结构',
      'expert.guide.s3.title': '03 采集截图',
      'expert.guide.s3.io': '输入：查看器上方工具条的「截图」按钮，再在弹窗预览里拖拽框选范围 → 产出：「复制选区」复制到剪贴板，或「下载 PNG」存成图片证据，并同时存下一份同名 JSON，记录任务、裁剪范围与截图时的视角',
      'expert.guide.s4.title': '04 判定题目',
      'expert.guide.s4.io': '输入：「任务校验」选 有效／无效／不确定，并在「校验反馈」写清理由 → 产出：这道题本身的质量结论，管理员据此返修题目',
      'expert.guide.s5.title': '05 作答',
      'expert.guide.s5.io': '输入：「答案」写结论，「推理过程」写关键判断依据，「尝试次数」记录这是第几次 → 产出：每次输入自动保存到本地，状态显示在表单下方',
      'expert.guide.s6.title': '06 提交',
      'expert.guide.s6.io': '输入：右上角「提交当前题」（在线）或「导出结果」（离线任务包）→ 产出：当前这一题回到管理员端等待质检，其它题可继续作答',
      'expert.guide.cameraNote': '查看器右上角那三个相机按钮来自 Mol* 自带的工具条，鼠标悬停即可看到中文提示：「重置缩放」（Reset Zoom）只改远近，把整个结构重新装进画面；「主轴摆正」（Orient Axes）按分子自身的主轴摆正，即把结构放平；「重置坐标轴」（Reset Axes）回到坐标系的 X／Y／Z 方向。后两个的差别就在于对齐的参照物：一个看分子形状，一个看坐标轴。',
      'expert.guide.measureNote': '要量原子间距离：点查看器右上角的「切换选择模式」（Toggle Selection Mode）进入选择模式，依次点中两个原子，再展开左侧面板 → 「结构工具」（Structure Tools）→「测量」（Measurements）→「距离」（Distance），画面上就会标出以 Å 为单位的距离；量完再点一次同一个按钮退出，避免后面的点击继续加选。',
      'expert.guide.symmetryNote': '结构文件常常只带非对称单元，周期镜像分子默认不在画面里。要看镜像：展开左侧面板 →「结构」（Structure）区的结构预设按钮，选「晶体接触」（Crystal Contacts，画出 5 Å 内的邻近镜像链，最适合判断最近接触）、「晶胞」（Unit Cell，填满整个晶胞）或「超胞」（Super Cell）；也可以在结构条目的「类型」（Type）里把「模型」（Model）换成「对称副本」（Symmetry Mates），并调其中的 radius 参数。这些入口只在文件带晶体对称信息时才出现——如果都找不到，说明该文件不含对称信息，请在「任务校验」里判为无效并写明原因。',
      'expert.guide.reasoningNote': '推理过程只记语义关键的那几步：真正改变你判断的操作（例如放大一次仍看不清，于是继续放大到原子层级），不用记录每一次鼠标移动。在「录制 Ground Truth」面板里保存一步本身就是这样一次关键操作，所以每一条保存下来的步骤都要写说明，缺一条就提交不了。',
      'expert.guide.next': '下一步：回到左侧列表继续下一题，已完成的任务会在列表里标出来。',
      'capture.viewerTitle': '3D 查看器',
      'capture.shot': '截图',
      'capture.shotCapturing': '正在截图…',
      'capture.shotTitle': '抓取整个 3D 查看器',
      'capture.shotUnavailable': '当前无法截图',
      'capture.dialogTitle': '裁剪截图',
      'capture.closeTitle': '关闭截图编辑器',
      'capture.reset': '重置选区',
      'capture.resetTitle': '把选区恢复为整张图像',
      'capture.copy': '复制选区',
      'capture.copyTitle': '把选区复制为 PNG',
      'capture.copyUnsupported': '当前浏览器环境不支持复制 PNG 到剪贴板',
      'capture.download': '下载 PNG',
      'capture.downloadTitle': '把选区下载为 PNG',
      'capture.canvasLabel': '截图裁剪预览，拖拽可框选区域',
      'capture.canvasSelected': '截图裁剪预览。已选择 {selectionWidth} × {selectionHeight} 像素，起点 {x}, {y}。拖拽可重新框选。',
      'capture.meta': '图像 {width} × {height} px ｜ 选区 {selectionWidth} × {selectionHeight} px，起点 {x}, {y}',
      'capture.status.viewerLoading': '查看器加载中…',
      'capture.status.noViewer': '尚未选择查看器',
      'capture.status.notReady': '查看器截图尚未就绪',
      'capture.status.capturingFull': '正在抓取整个查看器…',
      'capture.status.captured': '已截图',
      'capture.status.clipboardUnavailable': '当前环境无法复制到剪贴板，仍然可以下载。',
      'capture.status.selectionTooSmall': '选区太小，已保留上一个选区。',
      'capture.status.copying': '正在复制选区…',
      'capture.status.copied': '选区已复制到剪贴板。',
      'capture.status.preparing': '正在准备 PNG…',
      'capture.status.downloadStarted': 'PNG 与视角 JSON 已开始下载。',
      'capture.view.recorded': '视角信息将与 PNG 一起保存',
      'capture.view.unavailable': '此查看器不提供视角信息',
      'capture.error.copyFailed': '复制失败：{message}',
      'capture.error.downloadFailed': '下载失败：{message}',
      'capture.error.unknown': '未知截图错误。',
      'capture.error.decode': '截图 PNG 无法解码。',
      'capture.error.dimensions': '截图尺寸无效。',
      'capture.error.gone': '截图已失效。',
      'capture.error.canvasUnavailable': '当前浏览器无法创建裁剪画布。',
      'capture.error.encode': '选区无法编码为 PNG。',
      'manager.brandTag': '任务运营',
      'manager.navLabel': '管理视图',
      'manager.nav.workflow': '工作流',
      'manager.nav.setup': '准备',
      'manager.nav.setupHint': '人员与批次',
      'manager.nav.distribution': '分发',
      'manager.nav.distributionHint': '分配与任务包',
      'manager.nav.results': '结果',
      'manager.nav.resultsHint': '入库与导出',
      'manager.status.online': '管理端在线',
      'manager.status.onlineHint': '已认证工作区',
      'manager.adminDefault': '管理员',
      'manager.adminAccess': '管理员权限',
      'manager.signOut': '退出登录',
      'manager.eyebrow': '管理工作区',
      'manager.refresh': '刷新',
      'manager.refreshTitle': '刷新管理端状态',
      'manager.view.setup.title': '准备',
      'manager.view.setup.description': '专家与来源批次',
      'manager.view.distribution.title': '分发',
      'manager.view.distribution.description': '分配与专家任务包',
      'manager.view.results.title': '结果',
      'manager.view.results.description': '校验入库与导出',
      'manager.summaryLabel': '工作流概览',
      'manager.summary.experts': '专家',
      'manager.summary.profiles': '档案',
      'manager.summary.batches': '批次',
      'manager.summary.sources': '来源',
      'manager.summary.tasks': '任务',
      'manager.summary.imported': '已导入',
      'manager.summary.assignments': '分配',
      'manager.summary.bundles': '生效任务包',
      'manager.summary.published': '已发布',
      'manager.summary.results': '结果',
      'manager.summary.accepted': '已接收',
      'manager.activityLabel': '最新管理操作',
      'manager.activity': '操作记录',
      'manager.activityReady': '就绪。',
      'manager.setup.eyebrow': '基础',
      'manager.setup.heading': '专家与任务来源',
      'manager.setup.note': '名册与批次清单',
      'manager.reviewBundles.eyebrow': '管理员审阅',
      'manager.reviewBundles.heading': '审阅包',
      'manager.reviewBundles.note': '管理端数据库之外、已校验的本地来源',
      'manager.accounts.heading': '在线账号',
      'manager.accounts.note': '管理员与专家的登录账号',
      'manager.accounts.col.name': '姓名',
      'manager.accounts.col.username': '用户名',
      'manager.accounts.col.role': '角色',
      'manager.accounts.col.expertId': '专家 ID',
      'manager.accounts.col.access': '账号操作',
      'manager.expertImport.heading': '导入专家',
      'manager.expertImport.note': '专家名册',
      'manager.expertImport.fileLabel': '专家 CSV 文件',
      'manager.expertImport.csvLabel': 'CSV 内容',
      'manager.expertImport.submit': '导入专家',
      'manager.batchImport.heading': '导入任务批次',
      'manager.batchImport.note': '已校验的来源材料',
      'manager.batchImport.batchId': '批次 ID',
      'manager.batchImport.title': '标题',
      'manager.batchImport.tasksJsonl': '任务 JSONL 路径',
      'manager.batchImport.assetRoot': '资产根目录',
      'manager.batchImport.receipt': 'QC 准入回执',
      'manager.batchImport.purpose': '准入用途',
      'manager.batchImport.purposeReview': '专家评审分发',
      'manager.batchImport.viewerRoot': '查看器根目录',
      'manager.batchImport.submit': '预检并导入',
      'manager.credential.label': '一次性专家凭据',
      'manager.credential.warning': '请通过安全渠道转交该临时密码，它不会再次显示。',
      'manager.credential.dismiss': '收起',
      'manager.expertsPanel.heading': '专家',
      'manager.expertsPanel.countSuffix': '份档案',
      'manager.expertsPanel.filter': '筛选专家',
      'manager.expertsPanel.col.expertId': '专家 ID',
      'manager.expertsPanel.col.name': '姓名',
      'manager.expertsPanel.col.domains': '领域',
      'manager.expertsPanel.col.account': '账号',
      'manager.expertsPanel.col.action': '操作',
      'manager.expertsPanel.col.updated': '更新时间',
      'manager.batchesPanel.heading': '批次',
      'manager.batchesPanel.countSuffix': '个来源',
      'manager.batchesPanel.filter': '筛选批次',
      'manager.batchesPanel.col.batchId': '批次 ID',
      'manager.batchesPanel.col.title': '标题',
      'manager.batchesPanel.col.tasks': '任务数',
      'manager.batchesPanel.col.assigned': '已分配',
      'manager.batchesPanel.col.imported': '导入时间',
      'manager.distribution.eyebrow': '运营',
      'manager.distribution.heading': '分配与打包',
      'manager.distribution.note': '按领域均衡的任务分发',
      'manager.auto.heading': '自动均衡',
      'manager.auto.note': '按专家领域',
      'manager.auto.batch': '批次',
      'manager.auto.submit': '分配未分配任务',
      'manager.explicit.heading': '指定分配',
      'manager.explicit.note': '单任务指派单专家',
      'manager.explicit.task': '未分配任务',
      'manager.explicit.expert': '专家',
      'manager.explicit.submit': '分配任务',
      'manager.publish.heading': '发布给全部专家',
      'manager.publish.note': '原子切换生效任务包',
      'manager.publish.batch': '已完全分配的批次',
      'manager.publish.idle': '尚未执行发布。',
      'manager.publish.submit': '发布全部专家任务包',
      'manager.package.heading': '构建离线交付包',
      'manager.package.note': '精简包或独立包',
      'manager.package.batch': '批次',
      'manager.package.expert': '专家',
      'manager.package.mode': '交付模式',
      'manager.package.modeThin': '精简包',
      'manager.package.modeStandalone': '独立包',
      'manager.package.output': '新建输出目录',
      'manager.package.submit': '构建离线交付包',
      'manager.runtime.heading': '构建共享运行时',
      'manager.runtime.note': '安装一次，供精简包复用',
      'manager.runtime.viewerRoot': '查看器源目录',
      'manager.runtime.output': '新建运行时目录',
      'manager.runtime.submit': '构建共享运行时',
      'manager.legacy.heading': '导入历史任务包',
      'manager.legacy.note': '显式声明的预准入适配',
      'manager.legacy.root': '已有 v1 任务包目录',
      'manager.legacy.activate': '设为该专家的生效任务包',
      'manager.legacy.missingChecksums': '已审计但缺少 SHA256SUMS 的历史任务包',
      'manager.legacy.submit': '导入历史任务包',
      'manager.assignmentsPanel.heading': '分配',
      'manager.assignmentsPanel.countSuffix': '条任务归属',
      'manager.assignmentsPanel.filter': '筛选分配',
      'manager.assignmentsPanel.col.batch': '批次',
      'manager.assignmentsPanel.col.task': '任务',
      'manager.assignmentsPanel.col.domain': '领域',
      'manager.assignmentsPanel.col.expert': '专家',
      'manager.assignmentsPanel.col.status': '状态',
      'manager.assignmentsPanel.col.assignmentId': '分配 ID',
      'manager.assignmentsPanel.col.review': '质检',
      'manager.assignmentsPanel.filterBatch': '批次',
      'manager.assignmentsPanel.filterExpert': '专家',
      'manager.assignmentsPanel.filterStatus': '状态',
      'manager.assignmentsPanel.filterDomain': '领域',
      'manager.assignmentsPanel.filterAll': '全部',
      'manager.assignmentsPanel.filterActive': '当前显示 {shown} / {total} 条分配',
      'manager.assignmentsPanel.sampleSize': '抽检数量',
      'manager.assignmentsPanel.sample': '抽检',
      'manager.assignmentsPanel.sampleClear': '取消抽检',
      'manager.assignmentsPanel.sampleActive': '抽检 {shown} / {total} 条分配；抽检只改变当前视图，质检与打回照常写入审计日志',
      'manager.assignmentsPanel.sampleEmpty': '没有符合当前筛选条件的分配',
      'manager.activeBundlesPanel.heading': '生效专家任务包',
      'manager.activeBundlesPanel.countSuffix': '位专家可开始评审',
      'manager.activeBundlesPanel.filter': '筛选生效任务包',
      'manager.activeBundlesPanel.col.expert': '专家',
      'manager.activeBundlesPanel.col.batch': '批次',
      'manager.activeBundlesPanel.col.bundle': '任务包',
      'manager.activeBundlesPanel.col.tasks': '任务数',
      'manager.activeBundlesPanel.col.admission': '准入',
      'manager.activeBundlesPanel.col.activated': '生效时间',
      'manager.packagesPanel.heading': '独立包历史',
      'manager.packagesPanel.countSuffix': '个已登记的 v1 任务包',
      'manager.packagesPanel.filter': '筛选任务包',
      'manager.packagesPanel.col.batch': '批次',
      'manager.packagesPanel.col.expert': '专家',
      'manager.packagesPanel.col.assignments': '分配数',
      'manager.packagesPanel.col.packageId': '任务包 ID',
      'manager.packagesPanel.col.created': '创建时间',
      'manager.results.eyebrow': '收尾',
      'manager.results.heading': '结果入库与导出',
      'manager.results.note': '已校验的专家提交',
      'manager.resultsImport.heading': '导入结果',
      'manager.resultsImport.note': '专家提交的 JSON',
      'manager.resultsImport.fileLabel': '提交 JSON',
      'manager.resultsImport.submit': '校验并导入',
      'manager.resultsExport.heading': '导出结果',
      'manager.resultsExport.note': '汇总输出',
      'manager.resultsPanel.heading': '已导入结果',
      'manager.resultsPanel.countSuffix': '条已接收记录',
      'manager.resultsPanel.filter': '筛选结果',
      'manager.resultsPanel.col.task': '任务',
      'manager.resultsPanel.col.expert': '专家',
      'manager.resultsPanel.col.validation': '校验',
      'manager.resultsPanel.col.feedback': '反馈',
      'manager.resultsPanel.col.answer': '答案',
      'manager.resultsPanel.col.reasoning': '推理',
      'manager.resultsPanel.col.trajectory': '操作轨迹',
      'manager.resultsPanel.steps.summary': '{count} 步关键操作',
      'manager.reset.eyebrow': '账号访问',
      'manager.reset.confirmHeading': '重新签发临时密码',
      'manager.reset.note': '密码由服务端生成、只显示一次，专家首次登录时必须自行更换；该专家现有会话会被吊销。',
      'manager.reset.cancel': '取消',
      'manager.reset.confirm': '重新签发密码',
      'manager.reset.secretHeading': '新密码',
      'manager.reset.passwordLabel': '密码',
      'manager.reset.reveal': '显示',
      'manager.reset.hide': '隐藏',
      'manager.reset.clearOnClose': '关闭该弹窗会从本工作区清除该密码。',
      'manager.reset.copy': '复制',
      'manager.reset.close': '关闭',
      'manager.error.request': '请求失败。',
      'manager.error.accountUpdate': '账号更新失败。',
      'manager.error.stateLoad': '无法加载管理端状态。',
      'manager.error.reviewBundlesLoad': '无法加载质检任务包。',
      'manager.error.accountsLoad': '无法加载账号列表。',
      'manager.status.refreshed': '管理端状态已刷新。',
      'manager.summary.coverage': '覆盖率 {percent}%',
      'manager.account.status.notInvited': '未邀请',
      'manager.account.status.activationPending': '待激活',
      'manager.account.status.active': '已激活',
      'manager.account.status.offlineOnly': '仅离线',
      'manager.account.invite': '邀请',
      'manager.account.reissue': '重发临时密码',
      'manager.account.reissueConfirm': '确认为该专家重发密码？其现有会话将被吊销。',
      'manager.account.inviting': '正在创建邀请……',
      'manager.account.reissuing': '正在重发密码……',
      'manager.account.created': '临时密码已生成，只在上方安全面板中显示一次。',
      'manager.credential.durabilityWarning': '账号已更新，但目录持久化无法确认。请先记录该凭据，并在依赖它之前确认持久化存储。',
      'manager.assignment.approve': '通过',
      'manager.assignment.return': '打回',
      'manager.assignment.returnReasonPrompt': '请填写打回该分配的原因：',
      'manager.assignment.returnReasonRequired': '必须填写打回原因。',
      'manager.assignment.approveConfirm': '确认通过分配 {assignment}？',
      'manager.reviewBundle.type': '带校验和的静态任务包',
      'manager.reviewBundle.meta': '{count} 个任务 · 未入库 / 只读',
      'manager.reviewBundle.policy': '仅支持本地草稿与导出，无法集中提交。',
      'manager.reviewBundle.open': '打开查看器',
      'manager.reviewBundle.replay': '回放操作轨迹',
      'manager.accounts.reset': '重置密码',
      'manager.accounts.notResettable': '此处不可重置',
      'manager.reset.failed': '密码重置失败。',
      'manager.reset.incomplete': '密码重置响应不完整。',
      'manager.reset.durabilityPending': '密码已生效，但持久化存储确认仍在等待。',
      'manager.reset.clipboardUnavailable': '无法访问剪贴板。',
      'manager.reset.copied': '已复制。',
      'manager.reset.copyFailed': '复制失败。',
      'manager.publish.validating': '正在校验全部专家任务包，然后再激活……',
      'manager.publish.success': '已原子激活 {count} 个专家任务包。',
      'manager.publish.failed': '发布失败，已激活的任务包未发生变化。{error}',
      'manager.publish.refreshFailed': '发布已成功，但状态刷新失败；请点击刷新。',
      'manager.guide.summary': '操作指南：这一页做什么',
      'manager.guide.flow': '整体流程：导入专家名册 → 导入任务批次 → 分配任务 → 发布 → 专家在自己的工作台看到被分配的题目 → 回收并导入结果。',
      'manager.guide.setup.s1.title': '01 导入专家',
      'manager.guide.setup.s1.io': '输入：专家名册 CSV（expert_id、姓名、领域）→ 产出：可接收任务分配的专家档案',
      'manager.guide.setup.s2.title': '02 导入任务批次',
      'manager.guide.setup.s2.io': '输入：批次 ID、标题、任务 JSONL、素材根目录、准入凭证 → 产出：已准入的批次与可分配的任务清单',
      'manager.guide.setup.s3.title': '03 邀请专家账号',
      'manager.guide.setup.s3.io': '输入：在下方专家表格点「邀请」→ 产出：一次性临时密码，专家首次登录必须自行更换',
      'manager.guide.setup.next': '下一步：切到「分发」页，把批次里的任务分给专家。',
      'manager.guide.distribution.s1.title': '01 自动均衡',
      'manager.guide.distribution.s1.io': '输入：选择一个批次 → 产出：按专家领域均衡生成的分配记录',
      'manager.guide.distribution.s2.title': '02 指定分配',
      'manager.guide.distribution.s2.io': '输入：一个未分配任务加一位专家 → 产出：一条精确指定的分配记录',
      'manager.guide.distribution.s3.title': '03 发布给全部专家',
      'manager.guide.distribution.s3.io': '输入：选择一个批次 → 产出：原子切换后的生效专家任务包；只有发布后，专家登录才看得到题目',
      'manager.guide.distribution.s4.title': '04 构建离线交付包',
      'manager.guide.distribution.s4.io': '输入：批次、专家、输出路径与打包模式 → 产出：磁盘上的离线任务包',
      'manager.guide.distribution.s5.title': '05 构建共享运行时',
      'manager.guide.distribution.s5.io': '输入：viewer 根目录与输出路径 → 产出：可被多个精简包复用的查看器运行时',
      'manager.guide.distribution.s6.title': '06 导入历史任务包',
      'manager.guide.distribution.s6.io': '输入：历史任务包根目录，以及是否激活、是否允许缺失校验和 → 产出：登记进管理端的历史任务包',
      'manager.guide.distribution.review': '质检：下方「分配」表格中状态为 pending_review 的行会出现「通过 / 打回」按钮；打回必须填写原因，任务会退回该专家重做。用搜索框可按专家、状态、批次或领域筛选后抽检。',
      'manager.guide.distribution.next': '下一步：专家提交后，切到「结果」页导入并导出。',
      'manager.guide.results.s1.title': '01 导入结果',
      'manager.guide.results.s1.io': '输入：专家导出的提交 JSON 文件 → 产出：经校验后入库的结果记录',
      'manager.guide.results.s2.title': '02 导出结果',
      'manager.guide.results.s2.io': '输入：选择 JSON 或 CSV → 产出：包含全部已接收结果的汇总文件',
      'manager.guide.results.next': '下一步：在下方「已导入结果」表格里核对；需要返工的任务回到「分发」页打回。',
      'mcp.panel.title': '录制 Ground Truth',
      'mcp.panel.function': '功能',
      'mcp.panel.exploration': '显示非 GT 的探索类功能',
      'mcp.panel.run': '运行预览',
      'mcp.panel.running': '运行中…',
      'mcp.panel.runHint': '先重置查看器，重放已保存的 GT 步骤，再执行所选功能。在按下“保存步骤”之前不会保存任何内容。',
      'mcp.panel.rawReceipt': '原始回执',
      'mcp.panel.resultLabel': '查看器功能返回结果',
      'mcp.panel.tab.functions': '功能',
      'mcp.panel.tab.trajectory': '轨迹',
      'mcp.result.empty': '暂无结果。',
      'mcp.tool.waiting': '等待查看器…',
      'mcp.tool.none': '未选择查看器',
      'mcp.tool.noFunctions': '查看器没有可调用的功能',
      'mcp.tool.noCompliant': '没有符合任务策略的功能',
      'mcp.tool.marker.gt': '[GT 动作] ',
      'mcp.tool.marker.exploration': '[非 GT 探索] ',
      'mcp.tool.marker.utility': '[非 GT 工具] ',
      'mcp.tool.policy.gt': '[Ground Truth 动作｜可保存] ',
      'mcp.tool.policy.exploration': '[非 GT 探索｜不可保存] ',
      'mcp.tool.policy.utility': '[非 GT 工具｜不可保存] ',
      'mcp.field.include': '填写',
      'mcp.field.arrayItem': '{name} 第 {index} 项',
      'mcp.availability.waiting': '正在确认查看器支持的功能…',
      'mcp.availability.noTask': '请先选择任务，再查看可用的 Ground Truth 动作。',
      'mcp.availability.blocked': '已阻止：当前查看器没有暴露该任务允许的任何功能。非 GT 工具可以预览，但无法保存步骤。',
      'mcp.availability.ready.one': '有 {count} 个 Ground Truth 动作可用。工具类与探索类动作属于非 GT，无法保存。',
      'mcp.availability.ready.many': '有 {count} 个 Ground Truth 动作可用。工具类与探索类动作属于非 GT，无法保存。',
      'mcp.status.noViewer': '未选择查看器',
      'mcp.status.loading': '正在加载 {viewer} 的功能…',
      'mcp.status.counts': '{gt} 个 GT / {utility} 个工具 / {exploration} 个探索',
      'mcp.status.running': '{prefix}{name} 执行中…',
      'mcp.status.previewStage': '预览 / {label}…',
      'mcp.status.succeeded': '{prefix}{name} 执行成功',
      'mcp.stage.replay': '正在重放已保存的 GT 步骤',
      'mcp.stage.executing': '正在执行 {name}',
      'mcp.stage.capturing': '正在采集结果状态',
      'mcp.feedback.idle': '选择一个功能，然后点“运行预览”。',
      'mcp.feedback.idleNoGT': '可以选功能预览。这里的功能都无法保存为 GT。',
      'mcp.feedback.waitingViewer': '正在等待查看器上报可用功能…',
      'mcp.feedback.noTask': '请先选择任务，再查看可用的 Ground Truth 动作。',
      'mcp.feedback.starting': '{name}：开始执行（重置 → 重放 → 执行）…',
      'mcp.feedback.stage': '{name}：{label}…',
      'mcp.feedback.saveHint': '{name} 执行完成。按“保存步骤”即可记录为 Ground Truth。',
      'mcp.feedback.explorationOnly': '{name} 执行完成（仅探索，该步骤无法保存）。',
      'mcp.feedback.error': '{name}：{message}',
      'trajectory.phase.review': '复核',
      'trajectory.phase.trajectory': '轨迹',
      'trajectory.pendingAction.label': 'Ground Truth 预览动作',
      'trajectory.pending.none': '暂无预览',
      'trajectory.pending.uncommitted': '保存未提交。所选动作与截图均已保留，直接重试即可，无需重复操作。',
      'trajectory.pending.one': '距上次截图已有 {count} 次预览',
      'trajectory.pending.many': '距上次截图已有 {count} 次预览',
      'trajectory.pending.prompt': '先预览一个 Ground Truth 动作，再连同截图一起保存。',
      'trajectory.capture.save': '保存步骤 + 截图',
      'trajectory.capture.retrySave': '重试保存',
      'trajectory.capture.retryCapture': '重试截图 + 保存',
      'trajectory.final.label': '最终答案',
      'trajectory.action.export': '导出草稿',
      'trajectory.action.submit': '提交轨迹',
      'trajectory.action.submitUnconfigured': '提交未配置',
      'trajectory.action.reset': '全部重置',
      'trajectory.submit.title': '把轨迹提交到已配置的后端',
      'trajectory.submit.titleUnconfigured': '提交未配置：宿主页面需要注入 trajectory endpoint 的 meta 标签。',
      'trajectory.steps.heading': '已保存的 Ground Truth 步骤',
      'trajectory.steps.empty': '暂无已保存步骤。',
      'trajectory.delivery.initial': '可导出 / 未配置提交',
      'trajectory.delivery.summary': '已保存 {saved} 步 / {final} / 可导出 / {submit}',
      'trajectory.delivery.finalReady': '最终答案已填',
      'trajectory.delivery.finalMissing': '最终答案缺失',
      'trajectory.delivery.submitted': '已提交',
      'trajectory.delivery.submitReady': '可提交',
      'trajectory.delivery.submitUnavailable': '不可提交',
      'trajectory.step.title': '步骤 {step}',
      'trajectory.step.delete': '删除',
      'trajectory.step.deleteLabel': '删除步骤 {step}',
      'trajectory.step.screenshotAlt': '步骤 {step} 的查看器截图',
      'trajectory.step.action': 'Ground Truth 动作',
      'trajectory.step.actionBound': '该动作已与这张截图绑定。',
      'trajectory.step.inputLabel': '步骤 {step} 的功能入参',
      'trajectory.step.note': '步骤说明 / COT',
      'trajectory.step.noteRequired': '步骤说明 / COT（必填）',
      'trajectory.status.noTask': '未选择任务',
      'trajectory.status.recovering': '正在按已保存轨迹恢复查看器…',
      'trajectory.status.waitingViewer': '正在等待新的查看器文档…',
      'trajectory.status.storageUnavailable': '草稿存储不可用：{detail}',
      'trajectory.status.recoveryFailed': '查看器恢复失败：{detail}',
      'trajectory.status.locked': '标注已锁定：{detail}',
      'trajectory.status.lockedRollback': '回滚失败，标注已锁定：{detail}',
      'trajectory.status.selectAction': '保存前请先预览并选中一个 Ground Truth 动作。',
      'trajectory.status.saveStage': '保存 / {label}…',
      'trajectory.stage.replay': '正在重放已保存的 GT 步骤',
      'trajectory.stage.executing': '正在执行 {name}',
      'trajectory.stage.capturing': '正在截图',
      'trajectory.stage.committing': '正在写入截图与草稿',
      'trajectory.status.saveFailed': '保存未提交：{detail}。请重试，所选动作已保留。',
      'trajectory.status.saveFailedWithCapture': '保存未提交：{detail}。请重试，所选动作与截图均已保留。',
      'trajectory.status.stepSaved': '步骤 {step} 已连同截图保存',
      'trajectory.status.submitted.one': '已提交 {count} 个步骤',
      'trajectory.status.submitted.many': '已提交 {count} 个步骤',
      'trajectory.status.saved.one': '草稿已保存 / {count} 个步骤',
      'trajectory.status.saved.many': '草稿已保存 / {count} 个步骤',
      'trajectory.status.exported': '草稿已导出',
      'trajectory.status.needStep': '提交前至少要保存一个步骤。',
      'trajectory.status.needNote': '步骤 {step} 还缺少「这一步为什么重要」的说明。',
      'trajectory.status.needFinal': '提交前必须填写最终答案。',
      'trajectory.status.noTransport': '未配置提交通道。',
      'trajectory.status.submitAcknowledged': '轨迹提交已确认',
      'trajectory.confirm.reset': '清除所有已保存步骤与待处理预览，并重置查看器？',
      'error._frame': '操作被拒绝：{detail}',
      'error.authentication required': '身份认证失败，请检查用户名和密码。',
      'error.invalid username or password': '用户名或密码不正确。',
      'error.try again later': '尝试次数过多，请稍后再试。',
      'error.password change required': '请先完成密码激活。',
      'error.password activation is not authorized': '当前账号无需激活，或无权执行该操作。',
      'error.new password and confirmation do not match': '两次输入的新密码不一致。',
      'error.credential update failed': '密码写入失败，请联系管理员。',
      'error.cross-origin request rejected': '请求来源不受信任，已被拒绝。',
      'error.CSRF token is missing or invalid': '会话校验失败，请刷新页面后重试。',
      'error.role is not authorized': '当前账号的角色无权访问该功能。',
      'error.admin role required': '该操作仅管理员可执行。',
      'error.expert role required': '该操作仅专家账号可执行。',
      'error.administrator session is no longer active': '管理员会话已失效，请重新登录。',
      'error.credential update committed but could not be verified': '密码已写入但校验未通过，请重新登录确认；如仍失败请联系管理员。',
      'error.submission expert mismatch': '提交内容的专家身份与当前登录账号不一致。',
      'error.trajectory storage is not configured': '服务端未配置轨迹存储，请联系管理员后再提交。',
      'error.Idempotency-Key header disagrees with the payload': '幂等键与提交内容不一致，请刷新页面后重试。',
      'error.review bundle not found': '找不到对应的质检包。',
      'error.trajectory replay is unavailable for this review bundle': '该质检包不支持轨迹回放。',
      'error.format must be json or csv': '导出格式只能是 json 或 csv。',
      'error.unknown endpoint': '请求的接口不存在。',
      'error.unsupported audit filter(s): {}': '不支持的审计筛选条件：{0}。',
      // Gateway request shape and configuration.
      'error.Content-Type must be application/json': '请求的 Content-Type 必须是 application/json。',
      'error.invalid Content-Length': '请求的 Content-Length 无效。',
      'error.request body is empty or too large': '请求体为空或过大。',
      'error.request body must be an object': '请求体必须是一个 JSON 对象。',
      'error.login body must contain only username and password': '登录请求体只能包含 username 和 password。',
      'error.password change body must contain only new_password and confirm_password': '修改密码的请求体只能包含 new_password 和 confirm_password。',
      'error.assignment open body must contain assignment_id': '打开任务的请求体必须包含 assignment_id。',
      'error.submission must be an object': '提交内容必须是一个 JSON 对象。',
      'error.assignment review body must contain assignment_id, status, and optional reason': '质检请求体必须包含 assignment_id 和 status，reason 可选。',
      'error.review reason must be text': '质检意见必须是文本。',
      'error.account reset body must contain only expert_id': '重置账号的请求体只能包含 expert_id。',
      'error.account invite body must contain only expert_id': '邀请账号的请求体只能包含 expert_id。',
      'error.activate must be boolean': 'activate 必须是布尔值。',
      'error.allow_historical_missing_checksums must be boolean': 'allow_historical_missing_checksums 必须是布尔值。',
      'error.audit filters may be supplied only once': '每个审计筛选条件只能出现一次。',
      'error.audit limit and offset must be integers': '审计查询的 limit 和 offset 必须是整数。',
      'error.{} must be an HTTP(S) origin without a path': '{0} 必须是不含路径的 HTTP(S) 源地址。',
      'error.{} has an invalid port': '{0} 的端口无效。',
      'error.non-loopback {} must use HTTPS': '{0} 不是本机回环地址，必须使用 HTTPS。',
      'error.online port must be between 1 and 65535': '在线服务端口必须在 1 到 65535 之间。',
      'error.base_path must look like /sv-annotate without a trailing slash': 'base_path 必须形如 /sv-annotate，且结尾不能带斜杠。',
      'error.a public manager requires secure cookies': '管理端对外暴露时必须启用 secure cookie。',
      'error.session secret must contain at least 32 bytes': '会话密钥至少需要 32 字节。',
      'error.review_public_origin is required unless public_origin is the current http://127.0.0.1 loopback manager origin': '除管理端仍使用 http://127.0.0.1 本机回环地址外，必须配置 review_public_origin。',
      'error.review_public_origin must not be the manager public_origin': 'review_public_origin 不能与管理端的 public_origin 相同。',
      'error.review_public_origin must use a hostname distinct from public_origin': 'review_public_origin 的主机名必须与 public_origin 不同。',
      'error.a public manager requires an HTTPS, non-loopback review_public_origin': '管理端对外暴露时，review_public_origin 必须是 HTTPS 的非回环地址。',
      'error.public review mode MVP requires one bundle; configure a per-bundle review origin/router before exposing multiple bundles': '公网质检模式目前只支持一个质检包；要同时对外暴露多个质检包，请先为每个包配置独立的质检源地址或路由。',
      // Online accounts and credentials.
      'error.online credentials are not configured': '服务端尚未配置在线账号，请联系管理员。',
      'error.online credentials must be an object': '账号凭据文件必须是一个 JSON 对象。',
      'error.online credentials must contain users': '账号凭据文件必须包含 users 字段。',
      'error.unsupported online credentials schema': '不支持该账号凭据文件的格式版本。',
      'error.online credential user must be an object': '账号凭据中的每个用户都必须是一个 JSON 对象。',
      'error.online credential role must be admin or expert': '账号角色只能是 admin 或 expert。',
      'error.online credential password_version must be a positive integer': '账号凭据的 password_version 必须是正整数。',
      'error.online credential must_change_password must be boolean': '账号凭据的 must_change_password 必须是布尔值。',
      'error.online credential snapshot is out of date': '账号凭据已被其他操作更新，请刷新后重试。',
      'error.online account does not exist': '该在线账号不存在。',
      'error.duplicate online expert_id {}': '在线账号中的 expert_id 重复：{0}。',
      'error.duplicate online username {}': '在线账号中的用户名重复：{0}。',
      'error.expert must exist in the active roster before invitation': '邀请前该专家必须已在生效的专家名册中。',
      'error.expert already has an online account': '该专家已经有在线账号了。',
      'error.expert_id conflicts with an existing online username': 'expert_id 与已有的在线用户名冲突。',
      'error.expert display_name is required': '专家的 display_name 不能为空。',
      'error.expert account does not exist': '该专家账号不存在。',
      'error.password reset is available only for an existing expert account': '只能为已存在的专家账号重置密码。',
      'error.password activation is not required': '该账号无需激活密码。',
      'error.new password must not reuse the temporary password': '新密码不能与临时密码相同。',
      'error.password must be between {} and {} characters': '密码长度必须在 {0} 到 {1} 个字符之间。',
      'error.{} must be a regular non-symlink file': '{0} 必须是普通文件，不能是符号链接。',
      'error.{} must not be accessible by group or other users': '{0} 不能对同组用户或其他用户开放访问权限。',
      // Trajectory submissions from either rail.
      'error.trajectory payload must be an object': '轨迹提交内容必须是一个 JSON 对象。',
      'error.trajectory identity must be an object': '轨迹的身份信息必须是一个 JSON 对象。',
      'error.trajectory must be a list of steps': '轨迹必须是一个步骤列表。',
      'error.every trajectory step must be an object': '轨迹中的每一步都必须是一个 JSON 对象。',
      'error.trajectory {} must be non-empty text': '轨迹的 {0} 必须是非空文本。',
      'error.trajectory {} disagrees between identity and payload': '轨迹的 {0} 在身份信息与提交内容中不一致。',
      'error.trajectory schema_version must be {}': '轨迹的 schema_version 必须是 {0}。',
      'error.task {} is not in the task bundle for this expert': '任务 {0} 不在该专家的任务包内。',
      'error.trajectory idempotency key belongs to another expert': '该幂等键属于其他专家。',
      'error.trajectory idempotency key was reused for a different payload': '同一个幂等键被用于了不同的提交内容。',
      'error.trajectory submission_id was already recorded under a different idempotency key': '该 submission_id 之前已用另一个幂等键记录过。',
      // Manager store: submissions, batches, assignments and their lifecycle.
      'error.{} fields are invalid: {}': '{0} 中有字段不合法：{1}',
      'error.{} must be text of 1..{} characters': '{0} 必须是 1 到 {1} 个字符的文本。',
      'error.{} must be an object': '{0} 必须是一个 JSON 对象。',
      'error.{} must match {}': '{0} 必须与 {1} 一致。',
      'error.{} index is out of order': '{0} 的序号顺序不正确。',
      'error.trajectory must be a sciencevision.trajectory.v1 record or state collector_not_implemented': '轨迹必须是 sciencevision.trajectory.v1 记录，或状态为 collector_not_implemented。',
      'error.trajectory steps must be a list': '轨迹的 steps 必须是一个列表。',
      'error.trajectory carries more than {} steps': '轨迹的步骤数超过了 {0} 步的上限。',
      'error.trajectory status must be {} for {} steps': '有 {1} 步时，轨迹状态必须是 {0}。',
      // Audit trail.
      'error.audit payload must be an object': '审计记录必须是一个 JSON 对象。',
      'error.audit payload contains a sensitive field': '审计记录中含有敏感字段。',
      'error.audit payload for {} is invalid: {}': '{0} 的审计记录不合法：{1}',
      'error.audit payload {} is not a lifecycle status': '审计记录中的 {0} 不是合法的流转状态。',
      'error.audit payload attempt must be a positive integer': '审计记录的 attempt 必须是正整数。',
      'error.audit payload source is invalid': '审计记录的 source 不合法。',
      'error.audit payload reason must be 1 to 2000 characters': '审计记录的 reason 必须是 1 到 2000 个字符。',
      'error.audit payload must be canonical JSON': '审计记录必须是规范化 JSON。',
      'error.audit payload is too large': '审计记录过大。',
      'error.audit limit must be an integer between 1 and 200': '审计查询的 limit 必须是 1 到 200 之间的整数。',
      'error.audit offset must be an integer between 0 and 1000000': '审计查询的 offset 必须是 0 到 1000000 之间的整数。',
      'error.actor_role must be admin, expert, or system': 'actor_role 只能是 admin、expert 或 system。',
      // Assignment lifecycle.
      'error.assignment has unsupported lifecycle status {}': '任务分配处于不支持的流转状态 {0}。',
      'error.unsupported assignment status {}': '不支持的任务分配状态 {0}。',
      'error.illegal assignment transition {} -> {}': '任务分配不能从 {0} 直接流转到 {1}。',
      'error.expert is not authorized for this assignment transition': '专家账号无权执行该任务分配的这次状态流转。',
      'error.admin is not authorized for this assignment transition': '管理员无权执行该任务分配的这次状态流转。',
      'error.assignment status changed concurrently': '该任务分配的状态已被其他操作改动，请刷新后重试。',
      'error.assignment disappeared during status transition': '状态流转过程中该任务分配已不存在。',
      'error.assignment is not assigned to this expert': '该任务分配不属于当前专家。',
      'error.draft payload must be an object': '草稿内容必须是一个对象。',
      'error.drafts cannot be saved after the assignment is submitted': '任务提交后不能再保存草稿。',
      'error.validation_status must be valid, invalid, or uncertain': '校验状态必须是有效、无效或不确定。',
      'error.attempt must be an integer >= 1': '尝试次数必须是大于等于 1 的整数。',
      'error.steps must be a list': '关键操作必须是列表。',
      'error.expert has no active task bundle': '该专家没有生效的任务包。',
      'error.assignment is not part of the active task bundle': '该任务分配不在生效的任务包内。',
      'error.unknown assignment_id': '找不到该 assignment_id。',
      'error.review status must be approved or returned': '质检结论只能是 approved 或 returned。',
      'error.review reason must be 1 to 2000 characters': '质检意见必须是 1 到 2000 个字符。',
      'error.returning an assignment requires a reason': '打回任务必须填写原因。',
      // Expert roster import.
      'error.experts CSV requires columns: schema_version,expert_id,name,domains': '专家名册 CSV 必须包含这些列：schema_version,expert_id,name,domains。',
      'error.unexpected extra CSV column; quote values containing commas': 'CSV 出现了多余的列；含逗号的取值请用引号包起来。',
      'error.unsupported schema_version {}': '不支持的 schema_version {0}。',
      'error.name is required': 'name 不能为空。',
      'error.duplicate expert_id {}': 'expert_id 重复：{0}。',
      'error.line {}: {}': '第 {0} 行：{1}',
      'error.experts CSV has no data rows': '专家名册 CSV 没有数据行。',
      // Batch import: inputs, QC admission and task rows.
      'error.tasks JSONL cannot be a symlink: {}': '任务 JSONL 不能是符号链接：{0}',
      'error.asset root cannot be a symlink: {}': '素材根目录不能是符号链接：{0}',
      'error.QC admission receipt is required': '必须提供 QC 准入凭证。',
      'error.QC admission receipt cannot be a symlink: {}': 'QC 准入凭证不能是符号链接：{0}',
      'error.viewer root cannot be a symlink: {}': '渲染器根目录不能是符号链接：{0}',
      'error.tasks JSONL does not exist: {}': '任务 JSONL 不存在：{0}',
      'error.asset root does not exist: {}': '素材根目录不存在：{0}',
      'error.QC admission receipt does not exist: {}': 'QC 准入凭证不存在：{0}',
      'error.viewer root does not exist: {}': '渲染器根目录不存在：{0}',
      'error.tasks JSONL is empty': '任务 JSONL 是空的。',
      'error.admission receipt must be an object': '准入凭证必须是一个 JSON 对象。',
      'error.QC admission receipt: {}': 'QC 准入凭证：{0}',
      'error.domain is required': 'domain 不能为空。',
      'error.duplicate task_id {}': 'task_id 重复：{0}。',
      'error.conflicting hashes declared for asset {}': '素材 {0} 声明了互相冲突的哈希值。',
      'error.missing, linked, or escaped asset {}': '素材缺失、是链接、或超出了允许的目录范围：{0}',
      'error.asset byte_size mismatch for {}': '素材 {0} 的 byte_size 不一致。',
      'error.asset sha256 mismatch for {}': '素材 {0} 的 sha256 不一致。',
      'error.task row {}: {}': '第 {0} 条任务：{1}',
      'error.task domain(s) have no eligible active expert: {}': '以下任务领域没有可承接的在册专家：{0}',
      'error.batch {} predates QC admission; use a new batch_id with a passed receipt': '批次 {0} 早于 QC 准入机制；请换一个 batch_id，并附上通过的准入凭证。',
      'error.batch {} has an immutable QC admission receipt; use a new batch_id': '批次 {0} 的 QC 准入凭证不可变更；请换一个 batch_id。',
      'error.batch {} is immutable; use a new batch_id for changed tasks: {}': '批次 {0} 不可变更；任务有改动请换一个 batch_id：{1}',
      'error.batch {} has no passed QC admission receipt': '批次 {0} 没有通过的 QC 准入凭证。',
      'error.batch {} QC admission provenance is corrupt': '批次 {0} 的 QC 准入溯源信息已损坏。',
      // Assignment and publication.
      'error.unknown task {}/{}': '找不到任务 {0}/{1}。',
      'error.unknown or inactive expert {}': '专家 {0} 不存在或已停用。',
      'error.expert {} is not eligible for task domain {}': '专家 {0} 不能承接 {1} 领域的任务。',
      'error.task {} is already assigned to {}': '任务 {0} 已经分配给了 {1}。',
      'error.unknown batch {}': '找不到批次 {0}。',
      'error.unknown batch or expert': '找不到该批次或该专家。',
      'error.no active expert covers domain(s): {}': '没有在册专家覆盖这些领域：{0}',
      'error.expert {} has no assignments in {}': '专家 {0} 在 {1} 中没有任务分配。',
      'error.missing, linked, or escaped asset: {}': '素材缺失、是链接、或超出了允许的目录范围：{0}',
      'error.asset hash mismatch: {}': '素材哈希不一致：{0}',
      'error.asset byte_size mismatch: {}': '素材 byte_size 不一致：{0}',
      'error.batch {} still has {} unassigned task(s)': '批次 {0} 还有 {1} 条任务没有分配。',
      'error.batch {} has no assignments': '批次 {0} 没有任何任务分配。',
      'error.bundle_id already exists with different immutable content': '该 bundle_id 已存在，且内容与本次不同。',
      'error.cannot register an unsupported prepared bundle': '无法登记不受支持的任务包。',
      'error.no active task bundle for expert {}': '专家 {0} 没有生效的任务包。',
      'error.stored task bundle bytes failed their digest': '已存任务包的字节校验未通过。',
      'error.stored task bundle manifest digest mismatch': '已存任务包的清单摘要不一致。',
      // Packages, including legacy imports.
      'error.legacy package expert is not present in the manager': '历史交付包中的专家不在管理端记录中。',
      'error.legacy package batch is not present in the manager': '历史交付包中的批次不在管理端记录中。',
      'error.legacy package_id already exists with different immutable content': '该历史 package_id 已存在，且内容与本次不同。',
      'error.cannot register an unsupported package manifest': '无法登记不受支持的交付包清单。',
      'error.package manifest QC admission provenance mismatch': '交付包清单的 QC 准入溯源信息不一致。',
      'error.unknown package_id {}': '找不到 package_id {0}。',
      'error.package manifest hash mismatch': '交付包清单的哈希不一致。',
      'error.registered package assignment closure is invalid': '已登记交付包的任务分配集合不完整或不一致。',
      // Result submissions.
      'error.online submission actor does not own assignment': '提交者不是该任务分配的负责人。',
      'error.submission actor role is invalid': '提交者的角色不合法。',
      'error.assignment status {} does not allow submission': '任务分配处于 {0} 状态，不允许提交。',
      'error.online submission does not target the active task bundle': '本次提交对应的不是当前生效的任务包。',
      'error.submission actor_id and actor_role must be provided together': 'actor_id 和 actor_role 必须同时提供。',
      'error.submission actor_role must be admin, expert, or system': '提交的 actor_role 只能是 admin、expert 或 system。',
      'error.online submission actor does not match expert_id': '提交者与 expert_id 不一致。',
      'error.unsupported submission schema_version': '不支持该提交的 schema_version。',
      'error.unsupported contract_version': '不支持该 contract_version。',
      'error.submission is not canonical JSON': '提交内容不是规范化 JSON。',
      'error.submission payload_sha256 mismatch': '提交内容的 payload_sha256 不一致。',
      'error.package_manifest_sha256 must be a lowercase SHA-256': 'package_manifest_sha256 必须是小写的 SHA-256。',
      'error.results must be a non-empty list': 'results 必须是一个非空列表。',
      'error.submission {} does not match package': '提交的 {0} 与交付包不一致。',
      'error.submission_id already exists with different content': '该 submission_id 已存在，且内容与本次不同。',
      'error.result is not an object': '单条结果必须是一个 JSON 对象。',
      'error.unsupported result schema_version': '不支持该结果的 schema_version。',
      'error.result package_id mismatch': '结果的 package_id 不一致。',
      'error.result {} mismatch': '结果的 {0} 不一致。',
      'error.attempt must be a positive integer': 'attempt 必须是正整数。',
      'error.duplicate assignment_id + attempt in submission': '同一次提交中出现了重复的 assignment_id 与 attempt 组合。',
      'error.assignment_id is not part of this package': '该 assignment_id 不属于这个交付包。',
      'error.result {} does not match assignment': '结果的 {0} 与任务分配不一致。',
      'error.task_validation must be an object': 'task_validation 必须是一个 JSON 对象。',
      'error.task_validation.feedback must be text': 'task_validation.feedback 必须是文本。',
      'error.task_validation.status is invalid': 'task_validation.status 不合法。',
      'error.answer and reasoning must be text': 'answer 和 reasoning 必须是文本。',
      'error.evidence must be a list': 'evidence 必须是一个列表。',
      'error.saved_at must be non-empty timestamp text': 'saved_at 必须是非空的时间戳文本。',
      'error.result is not canonical JSON': '结果不是规范化 JSON。',
      'error.assignment_id + attempt already exists in the v2 exchange': '该 assignment_id 与 attempt 组合已存在于 v2 交换记录中。',
      'error.assignment_id + attempt already exists in the v1 exchange': '该 assignment_id 与 attempt 组合已存在于 v1 交换记录中。',
      'error.assignment_id + attempt already exists with different content': '该 assignment_id 与 attempt 组合已存在，且内容与本次不同。',
      'error.online submission has multiple attempts for one assignment': '同一个任务分配在本次提交中出现了多个 attempt。',
      'error.v2 submission bundle_id must identify a v2 bundle': 'v2 提交的 bundle_id 必须指向一个 v2 任务包。',
      'error.bundle_manifest_sha256 must be a lowercase SHA-256': 'bundle_manifest_sha256 必须是小写的 SHA-256。',
      'error.unknown bundle_id {}': '找不到 bundle_id {0}。',
      'error.v2 submissions require a v2 task bundle': 'v2 提交需要对应的 v2 任务包。',
      'error.submission {} does not match bundle': '提交的 {0} 与任务包不一致。',
      'error.bundle manifest hash mismatch': '任务包清单的哈希不一致。',
      'error.registered bundle assignment closure is invalid': '已登记任务包的任务分配集合不完整或不一致。',
      'error.result bundle_id mismatch': '结果的 bundle_id 不一致。',
      'error.assignment_id is not part of this bundle': '该 assignment_id 不属于这个任务包。',
      // Manager database health, checked at startup and after migration.
      'error.manager database has unsupported schema migration(s): {}': '管理端数据库存在不受支持的迁移记录：{0}',
      'error.manager database lifecycle status column is unhealthy': '管理端数据库的流转状态列不健康。',
      'error.manager database audit_events table is unhealthy': '管理端数据库的 audit_events 表不健康。',
      'error.manager database lifecycle triggers are unhealthy': '管理端数据库的流转触发器不健康。',
      'error.submitted assignment lifecycle backfill is incomplete': '已提交任务分配的流转状态回填尚未完成。',
      'error.assignment status insert guard is unhealthy': '任务分配状态的写入保护不健康。',
      'error.assignment status transition guard is unhealthy': '任务分配状态的流转保护不健康。',
      'error.audit event replacement guard is unhealthy': '审计记录的防改写保护不健康。',
      'error.assignment replacement guard is unhealthy': '任务分配的防改写保护不健康。',
      // Task and admission-receipt contract.
      'error.task must be an object': '任务记录必须是一个 JSON 对象。',
      'error.{} must be a lowercase SHA-256': '{0} 必须是小写的 SHA-256。',
      'error.{} must match {}; display names are not IDs': '{0} 必须符合 {1}；显示名称不能当作 ID 使用。',
      'error.{} must be non-empty text': '{0} 必须是非空文本。',
      'error.{} must be text': '{0} 必须是文本。',
      'error.{} must be a list': '{0} 必须是一个列表。',
      'error.{} must be a list of text values': '{0} 必须是一个文本列表。',
      'error.{} contains a non-finite number': '{0} 中含有非有限数值。',
      'error.{} contains an unsupported option': '{0} 中含有不支持的选项。',
      'error.{}.{} must be boolean': '{0}.{1} 必须是布尔值。',
      'error.{}.{} must be text': '{0}.{1} 必须是文本。',
      'error.{}.{} must be text or a number': '{0}.{1} 必须是文本或数字。',
      'error.{}.{} must be a non-negative integer': '{0}.{1} 必须是非负整数。',
      'error.{}.{} contains a non-finite number': '{0}.{1} 中含有非有限数值。',
      'error.{}.max_steps must be a non-negative integer': '{0}.max_steps 必须是非负整数。',
      'error.{}.role must be text': '{0}.role 必须是文本。',
      'error.{}.mask_id must be text or an integer': '{0}.mask_id 必须是文本或整数。',
      'error.{}.view_hint must be an object': '{0}.view_hint 必须是一个 JSON 对象。',
      'error.{}.view_hint.cam must be text': '{0}.view_hint.cam 必须是文本。',
      'error.domains must be a delimited string or list': 'domains 必须是分隔字符串或列表。',
      'error.at least one domain is required': '至少需要一个 domain。',
      'error.source task {} has conflicting source_revision_sha256 values': '源任务 {0} 的 source_revision_sha256 取值互相冲突。',
      'error.admission receipt digest mismatch': '准入凭证的摘要不一致。',
      'error.unsupported admission receipt schema_version': '不支持该准入凭证的 schema_version。',
      'error.admission receipt status is not passed': '准入凭证的状态不是 passed。',
      'error.unsupported expected admission purpose {}': '不支持的准入用途 {0}。',
      'error.admission receipt purpose is not expected {}': '准入凭证的用途不是预期的 {0}。',
      'error.admission receipt tasks.count must be a positive integer': '准入凭证的 tasks.count 必须是正整数。',
      'error.admission receipt tasks.sha256 does not match tasks JSONL': '准入凭证的 tasks.sha256 与任务 JSONL 不一致。',
      'error.admission receipt tasks.count does not match tasks JSONL': '准入凭证的 tasks.count 与任务 JSONL 不一致。',
      'error.unsupported admission receipt task revision scope': '不支持该准入凭证的任务修订范围。',
      'error.unsupported admission receipt task revision algorithm': '不支持该准入凭证的任务修订算法。',
      'error.admission receipt task_revision_set.count must be a positive integer': '准入凭证的 task_revision_set.count 必须是正整数。',
      'error.admission receipt task revision count does not match tasks JSONL': '准入凭证的任务修订数量与任务 JSONL 不一致。',
      'error.admission receipt task revision digest does not match tasks JSONL': '准入凭证的任务修订摘要与任务 JSONL 不一致。',
      // Asset references inside a task.
      'error.assets must be a list': 'assets 必须是一个列表。',
      'error.assets must be a list of objects': 'assets 必须是一个由 JSON 对象组成的列表。',
      'error.asset must be an object': '单个素材必须是一个 JSON 对象。',
      'error.task must declare at least one asset object': '任务至少要声明一个素材对象。',
      'error.asset object is missing path or repo_path': '素材对象缺少 path 或 repo_path。',
      'error.asset path must be a non-empty string': '素材路径必须是非空字符串。',
      'error.unsafe asset path: {}': '素材路径不安全：{0}',
      'error.remote asset paths are forbidden: {}': '不允许使用远程素材路径：{0}',
      'error.asset path overlaps package control data: {}': '素材路径与交付包的控制数据冲突：{0}',
      'error.asset path overlaps a package control file: {}': '素材路径与交付包的控制文件冲突：{0}',
      'error.asset path and repo_path must identify the same local file': 'path 与 repo_path 必须指向同一个本地文件。',
      'error.mask_path and mask_repo_path must identify the same local file': 'mask_path 与 mask_repo_path 必须指向同一个本地文件。',
      'error.asset {} is missing a valid sha256': '素材 {0} 缺少合法的 sha256。',
      'error.asset {} has an invalid byte_size': '素材 {0} 的 byte_size 不合法。',
      'error.asset {} has a negative byte_size': '素材 {0} 的 byte_size 是负数。',
      'error.conflicting sizes declared for asset {}': '素材 {0} 声明了互相冲突的大小。',
      'error.asset_hashes must be a list': 'asset_hashes 必须是一个列表。',
      'error.asset_hashes must be a non-empty list': 'asset_hashes 必须是一个非空列表。',
      'error.asset_hashes[{}] must be an object': 'asset_hashes[{0}] 必须是一个 JSON 对象。',
      'error.asset_hashes[{}].{} must be text': 'asset_hashes[{0}].{1} 必须是文本。',
      'error.asset_hashes[{}].byte_size must be a non-negative integer': 'asset_hashes[{0}].byte_size 必须是非负整数。',
      'error.asset_hashes entries must include repo_path and sha256': 'asset_hashes 的每一项都必须含有 repo_path 和 sha256。',
      'error.asset_hashes must exactly match public asset references ({})': 'asset_hashes 必须与公开引用的素材完全一致（{0}）。',
      // Viewer runtime and task bundles.
      'error.viewer runtime root must be a non-symlink directory': '渲染器运行时根目录必须是普通目录，不能是符号链接。',
      'error.viewer runtime contains a symlink: {}': '渲染器运行时中含有符号链接：{0}',
      'error.viewer runtime is empty': '渲染器运行时是空的。',
      'error.viewer runtime entrypoint index.html is missing or linked': '渲染器运行时的入口 index.html 缺失或是链接。',
      'error.viewer runtime is incomplete: {}': '渲染器运行时不完整：{0}',
      'error.viewer runtime has an executable network dependency: {}': '渲染器运行时存在需要联网的可执行依赖：{0}',
      'error.viewer runtime page does not load the screenshot provider: {}': '渲染器页面没有加载截图能力：{0}',
      'error.viewer runtime is missing assigned renderer: {}.html': '渲染器运行时缺少所分配的渲染页：{0}.html。',
      'error.viewer runtime loader API does not satisfy bundle requirements': '渲染器运行时的 loader API 不满足任务包的要求。',
      'error.viewer runtime {} inventory is invalid': '渲染器运行时的 {0} 清单不合法。',
      'error.viewer runtime is missing required {}: {}': '渲染器运行时缺少必需的 {0}：{1}',
      'error.viewer/molstar.html lacks the expert-layout injection anchor': 'viewer/molstar.html 缺少专家版布局的注入锚点。',
      'error.unsafe viewer name in task: {}': '任务中的渲染器名称不安全：{0}',
      'error.bundle runtime_requirement must be an object': '任务包的 runtime_requirement 必须是一个 JSON 对象。',
      'error.bundle runtime_requirement.loader_api must be text': '任务包的 runtime_requirement.loader_api 必须是文本。',
      'error.bundle runtime_requirement.{} must be text values': '任务包的 runtime_requirement.{0} 必须是文本取值。',
      'error.{} must be a non-empty relative POSIX path': '{0} 必须是非空的相对 POSIX 路径。',
      'error.unsafe {}: {}': '{0} 不安全：{1}',
      'error.asset closure conflict: {}': '素材闭包冲突：{0}',
      // Packaging.
      'error.package mode must be standalone or thin': '打包模式只能是 standalone 或 thin。',
      'error.batch {} still has {} unassigned task(s): {}': '批次 {0} 还有 {1} 条任务没有分配：{2}',
      'error.copied asset hash mismatch: {}': '复制后的素材哈希不一致：{0}',
      'error.copied asset byte_size mismatch: {}': '复制后的素材 byte_size 不一致：{0}',
      // Legacy package import.
      'error.legacy package SHA256SUMS is required; use the explicit historical missing-checksum policy only for audited pre-checksum packages': '历史交付包必须带 SHA256SUMS；只有经过审计的、早于校验和机制的包才可以使用显式的历史缺失校验和策略。',
      'error.legacy package SHA256SUMS must be a regular file': '历史交付包的 SHA256SUMS 必须是普通文件。',
      'error.invalid SHA256SUMS line {}: {}': 'SHA256SUMS 第 {0} 行不合法：{1}',
      'error.invalid SHA256SUMS line {}': 'SHA256SUMS 第 {0} 行不合法。',
      'error.legacy package SHA256SUMS inventory mismatch': '历史交付包的 SHA256SUMS 清单与实际文件不一致。',
      'error.legacy package SHA256SUMS omits {}': '历史交付包的 SHA256SUMS 遗漏了 {0}。',
      'error.legacy package checksum mismatch: {}': '历史交付包的校验和不一致：{0}',
      'error.unsupported legacy integrity policy: {}': '不支持的历史完整性策略：{0}',
      'error.legacy package root must be a non-symlink directory': '历史交付包根目录必须是普通目录，不能是符号链接。',
      'error.legacy package contains a symlink: {}': '历史交付包中含有符号链接：{0}',
      'error.legacy package manifest is missing': '历史交付包缺少清单文件。',
      'error.legacy package manifest schema is unsupported': '不支持该历史交付包清单的格式版本。',
      'error.legacy package tasks file is missing or linked': '历史交付包的任务文件缺失或是链接。',
      'error.legacy package task count mismatch': '历史交付包的任务数量不一致。',
      'error.legacy package task identity mismatch': '历史交付包的任务标识不一致。',
      'error.legacy package assignment closure is missing': '历史交付包缺少任务分配闭包。',
      'error.legacy package task assignment closure mismatch': '历史交付包的任务分配闭包与任务不一致。',
      'error.legacy package asset closure is missing': '历史交付包缺少素材闭包。',
      'error.legacy package asset record must be an object': '历史交付包的素材记录必须是一个 JSON 对象。',
      'error.legacy package asset is missing or linked: {}': '历史交付包的素材缺失或是链接：{0}',
      'error.legacy package asset hash mismatch: {}': '历史交付包的素材哈希不一致：{0}',
      'error.legacy package asset size mismatch: {}': '历史交付包的素材大小不一致：{0}',
      // Public review bundles.
      'error.{} must be a safe root-relative URL path': '{0} 必须是安全的、相对站点根的 URL 路径。',
      'error.{} must start with exactly one slash': '{0} 必须以且仅以一个斜杠开头。',
      'error.{} contains an unsafe path component': '{0} 含有不安全的路径片段。',
      'error.review bundle SHA256SUMS must be a regular non-symlink file': '质检包的 SHA256SUMS 必须是普通文件，不能是符号链接。',
      'error.invalid review bundle SHA256SUMS line {}': '质检包 SHA256SUMS 第 {0} 行不合法。',
      'error.duplicate or reserved SHA256SUMS path at line {}': 'SHA256SUMS 第 {0} 行的路径重复或属于保留路径。',
      'error.review bundle contains a symlink: {}': '质检包中含有符号链接：{0}',
      'error.review bundle checksum inventory mismatch; {}': '质检包的校验和清单不一致；{0}',
      'error.review bundle checksum mismatch: {}': '质检包的校验和不一致：{0}',
      'error.review bundle {} title is required': '质检包 {0} 必须填写标题。',
      'error.review bundle {} port must be 0..65535': '质检包 {0} 的端口必须在 0 到 65535 之间。',
      'error.review bundle {} root must be an absolute path': '质检包 {0} 的根目录必须是绝对路径。',
      'error.review bundle {} root must be a non-symlink directory': '质检包 {0} 的根目录必须是普通目录，不能是符号链接。',
      'error.review bundle {} omits bundle_manifest.json': '质检包 {0} 缺少 bundle_manifest.json。',
      'error.review bundle {} manifest is invalid JSON': '质检包 {0} 的清单不是合法 JSON。',
      'error.review bundle {} manifest kind is unsupported': '不支持质检包 {0} 的清单类型。',
      'error.review bundle {} tasks_jsonl is outside its inventory': '质检包 {0} 的 tasks_jsonl 不在其清单范围内。',
      'error.review bundle {} omits {}': '质检包 {0} 缺少 {1}。',
      'error.review bundle {} task_count is invalid': '质检包 {0} 的 task_count 不合法。',
      'error.review bundle {} tasks_jsonl is invalid': '质检包 {0} 的 tasks_jsonl 不合法。',
      'error.review bundle {} task_count does not match JSONL': '质检包 {0} 的 task_count 与 JSONL 不一致。',
      'error.review bundles config must be a regular non-symlink file': '质检包配置必须是普通文件，不能是符号链接。',
      'error.review bundles config must not be accessible by group or other users': '质检包配置不能对同组用户或其他用户开放访问权限。',
      'error.review bundles config is invalid JSON': '质检包配置不是合法 JSON。',
      'error.unsupported review bundles config schema': '不支持该质检包配置的格式版本。',
      'error.review bundles config must contain a bundles list': '质检包配置必须包含 bundles 列表。',
      'error.review bundles config record {} must be an object': '质检包配置的第 {0} 项必须是一个 JSON 对象。',
      'error.duplicate review_id {}': 'review_id 重复：{0}。',
      // Result export and the local console.
      'error.manager result row {} fields are invalid': '管理端结果第 {0} 行的字段不合法。',
      'error.manager result row {} has an invalid v1 container binding': '管理端结果第 {0} 行的 v1 归属信息不合法。',
      'error.manager result row {} has an invalid v2 container binding': '管理端结果第 {0} 行的 v2 归属信息不合法。',
      'error.manager result row {} has an unsupported schema_version': '管理端结果第 {0} 行的 schema_version 不受支持。',
      'error.export format must be json or csv': '导出格式只能是 json 或 csv。',
      'error.loopback Host required': '该接口只接受来自本机的请求。',
      'error.port must be between 0 and 65535': '端口必须在 0 到 65535 之间。',
      // Wording the browser workspace throws at the expert: the MCP function
      // panel and the trajectory drawer. Keyed by the same English wording.
      'error.Trajectory task requires a task_id.': '轨迹标注需要 task_id。',
      'error.Trajectory controls require MCP controls and a trajectory root.': '轨迹控件需要 MCP 控件和轨迹容器节点。',
      'error.Start trajectory annotation before saving a step.': '请先进入轨迹标注阶段，再保存步骤。',
      'error.Preview receipt is invalid.': '预演凭据不合法。',
      'error.Preview receipt requires a positive completion_seq.': '预演凭据的 completion_seq 必须是正整数。',
      'error.Select exactly one preview action for this step.': '这一步只能选择一个预演动作。',
      'error.The selected preview was invalidated by a failed or unknown viewer event.': '所选预演已失效：渲染器出现了失败或无法识别的事件。',
      'error.The selected preview is not allowed by the current task policy.': '当前任务的策略不允许所选的这个预演动作。',
      'error.The selected preview belongs to a different viewer instance.': '所选预演属于另一个渲染器实例。',
      'error.The retained retry action is no longer allowed by the current task.': '保留下来待重试的动作已不被当前任务允许。',
      'error.The retained retry action belongs to a different viewer instance.': '保留下来待重试的动作属于另一个渲染器实例。',
      'error.Save step sequence boundary is invalid.': '保存步骤的序号边界不合法。',
      'error.A valid viewer result_state is required to save a step.': '保存步骤需要渲染器返回合法的 result_state。',
      'error.A valid monotonic cut_seq is required to save a step.': '保存步骤需要一个合法且递增的 cut_seq。',
      'error.A valid capture boundary sequence is required to save a step.': '保存步骤需要一个合法的截图边界序号。',
      'error.Run at least one task-compliant Preview before saving a step.': '保存步骤前，请至少执行一次符合任务要求的预演。',
      'error.A PNG screenshot is required to save a step.': '保存步骤需要一张 PNG 截图。',
      'error.Selected action is not task-compliant in this screenshot interval.': '在这段截图区间内，所选动作不符合任务要求。',
      'error.Trajectory step was not found.': '找不到这条轨迹步骤。',
      'error.A saved ground-truth action is immutable because its screenshot is already bound.': '已保存的 ground-truth 动作不可修改，它的截图已经绑定。',
      // Viewer and screenshot.
      'error.viewer_get_state returned no usable state.': 'viewer_get_state 没有返回可用的状态。',
      'error.Committed viewer action returned no usable result state.': '已提交的渲染器操作没有返回可用的结果状态。',
      'error.Viewer changed while the operation was running.': '操作执行期间渲染器发生了变化。',
      'error.Viewer screenshot capture is unavailable.': '渲染器截图功能不可用。',
      'error.Viewer screenshot capture returned no image.': '渲染器截图没有返回图像。',
      'error.Screenshot capture is unavailable for this viewer.': '该渲染器不支持截图。',
      'error.Ground-truth screenshot capture must return a PNG blob.': 'ground-truth 截图必须返回 PNG 数据。',
      'error.Rebuilt trajectory screenshot must be PNG.': '重建的轨迹截图必须是 PNG。',
      'error.Unable to encode screenshot.': '截图编码失败。',
      'error.Viewer returned no callable functions.': '渲染器没有返回可调用的功能。',
      'error.Viewer function controls require a document.': '渲染器功能面板需要一个 document。',
      'error.Viewer function controls require an iframe and controls root.': '渲染器功能面板需要一个 iframe 和面板容器节点。',
      'error.Default viewer function controls are unavailable.': '默认的渲染器功能面板不可用。',
      'error.Trajectory runtime is unavailable.': '轨迹运行时不可用。',
      'error.Unknown viewer function error.': '渲染器功能出现了未知错误。',
      // Function arguments typed in the panel.
      'error.{} is required.': '{0} 不能为空。',
      'error.{} must be a finite number.': '{0} 必须是有限数值。',
      'error.{} must be an integer.': '{0} 必须是整数。',
      'error.{}[{}] must be a finite number.': '{0}[{1}] 必须是有限数值。',
      'error.{} must be valid JSON: {}': '{0} 必须是合法 JSON：{1}',
      'error.{} is no longer allowed by the current task.': '当前任务已不允许 {0}。',
      // Local draft storage.
      'error.IndexedDB draft storage is unavailable.': 'IndexedDB 草稿存储不可用。',
      'error.Unable to open annotation storage.': '无法打开标注存储。',
      'error.Annotation storage transaction failed.': '标注存储的事务失败。',
      'error.Annotation storage transaction aborted.': '标注存储的事务被中止。',
      'error.Annotation storage request failed.': '标注存储的请求失败。',
      // Trajectory submission.
      'error.Submission transport is not configured.': '尚未配置提交通道。',
      'error.Trajectory submission endpoint is invalid.': '轨迹提交地址不合法。',
      'error.Trajectory submission endpoint must be same-origin.': '轨迹提交地址必须同源。',
      'error.Fetch is unavailable for trajectory submission.': '当前环境不支持 fetch，无法提交轨迹。',
      'error.Trajectory submission requires an idempotency key.': '轨迹提交需要一个幂等键。',
      'error.Trajectory submission failed with HTTP {}.': '轨迹提交失败，HTTP 状态码 {0}。',
      'error.Trajectory submission returned invalid JSON.': '轨迹提交返回的不是合法 JSON。',
      'error.Submission was not acknowledged by the backend.': '服务端没有确认这次提交。',
      'error.Submission acknowledgment does not match this submission ID.': '服务端确认的 submission ID 与本次提交不一致。',
      // The viewer declares its own functions, and it declares them in English:
      // the names, the argument names, the choices in a dropdown and the sentence
      // explaining each one all arrive over the iframe boundary at runtime. The wire
      // names stay as they are, because a task names its allowed functions by them
      // and a receipt records them; what a person reads is keyed by that same text.
      // Chinese only, so an English reader keeps the viewer wording verbatim.
      // A gloss shown beside each function name in the dropdown.
      'viewer.tool.viewer_rotate': '旋转视角',
      'viewer.tool.viewer_zoom': '缩放',
      'viewer.tool.viewer_pan': '平移',
      'viewer.tool.viewer_slice': '切换断层',
      'viewer.tool.viewer_crosshair': '移动十字准线',
      'viewer.tool.viewer_set_view': '切换视图模式',
      'viewer.tool.viewer_window_level': '调窗',
      'viewer.tool.viewer_bbox': '计算包围盒',
      'viewer.tool.viewer_clip_plane': '设置剖切面',
      'viewer.tool.viewer_visibility': '显示或隐藏',
      'viewer.tool.viewer_highlight': '高亮',
      'viewer.tool.viewer_focus': '聚焦',
      'viewer.tool.viewer_switch_structure': '切换结构',
      'viewer.tool.viewer_measure_distance': '测量距离',
      'viewer.tool.viewer_reset': '复位',
      'viewer.tool.viewer_get_state': '读取当前状态',
      // A gloss for an argument name, shown when the label is hovered.
      'viewer.arg.d_azimuth_deg': '方位角增量（度）',
      'viewer.arg.d_elevation_deg': '俯仰角增量（度）',
      'viewer.arg.d_roll_deg': '滚转角增量（度）',
      'viewer.arg.azimuth_deg': '方位角（度）',
      'viewer.arg.elevation_deg': '俯仰角（度）',
      'viewer.arg.factor': '倍数',
      'viewer.arg.delta_mm': '平移增量（毫米）',
      'viewer.arg.axis': '断层方向',
      'viewer.arg.index': '体素序号',
      'viewer.arg.fraction': '归一化位置',
      'viewer.arg.mode': '视图模式',
      'viewer.arg.space': '坐标空间',
      'viewer.arg.position': '坐标',
      'viewer.arg.center': '窗位',
      'viewer.arg.width': '窗宽',
      'viewer.arg.layer': '图层',
      'viewer.arg.visible': '是否可见',
      'viewer.arg.opacity': '不透明度',
      'viewer.arg.enabled': '是否启用',
      'viewer.arg.depth': '剖切深度',
      'viewer.arg.selector': '选择器',
      'viewer.arg.structure_id': '结构 ID',
      'viewer.arg.clear': '清除',
      'viewer.arg.a': '原子 A',
      'viewer.arg.b': '原子 B',
      // The choices in an argument dropdown. The value sent stays the English one.
      'viewer.enum.axial': '轴位',
      'viewer.enum.coronal': '冠状位',
      'viewer.enum.sagittal': '矢状位',
      'viewer.enum.render': '三维渲染',
      'viewer.enum.multiplanar': '多平面',
      'viewer.enum.voxel': '体素',
      'viewer.enum.fraction': '归一化',
      // The sentence the viewer supplies for a function or an argument.
      'viewer.help.Increment the molecular camera azimuth, elevation, and roll in degrees around its current target.': '以当前目标为中心，按度数增量调整分子相机的方位角、俯仰角与滚转角。',
      'viewer.help.Increment the 3D volume-render azimuth/elevation in degrees. Non-zero roll is unsupported by NiiVue.': '按度数增量调整三维体渲染的方位角与俯仰角。NiiVue 不支持非零滚转角。',
      'viewer.help.Incremental azimuth in degrees.': '方位角增量，单位为度。',
      'viewer.help.Incremental elevation in degrees.': '俯仰角增量，单位为度。',
      'viewer.help.Must be 0; NiiVue has no roll control.': '必须为 0：NiiVue 没有滚转控制。',
      'viewer.help.Multiply camera zoom; factor > 1 moves closer and factor < 1 moves farther away.': '按倍数缩放相机：大于 1 靠近，小于 1 远离。',
      'viewer.help.Multiply 2D and 3D zoom; factor > 1 zooms in.': '按倍数缩放二维与三维视图：大于 1 为放大。',
      'viewer.help.Increment the 2D pan vector in world millimeters.': '按世界坐标毫米数增量平移二维视图。',
      'viewer.help.Show an anatomical slice and move the crosshair by voxel index or normalized fraction.': '显示指定解剖断层，并按体素序号或归一化位置移动十字准线。',
      'viewer.help.Inclusive zero-based voxel index.': '体素序号，从 0 开始且含端点。',
      'viewer.help.Normalized slice position.': '归一化的断层位置。',
      'viewer.help.Move the crosshair using a fixed three-number voxel or normalized-fraction coordinate.': '用三个数字组成的体素坐标或归一化坐标移动十字准线。',
      'viewer.help.[x, y, z] in voxel or normalized fraction space.': '体素空间或归一化空间中的 [x, y, z]。',
      'viewer.help.Switch between 3D render, MPR, or a single anatomical plane.': '在三维渲染、多平面重建与单一解剖平面之间切换。',
      'viewer.help.Set display window center and positive width for a volume layer.': '设置某个体数据图层的显示窗位与窗宽，窗宽须为正数。',
      'viewer.help.Compute the inclusive non-zero voxel bounding box of an overlay/mask layer; optionally center the crosshair.': '计算叠加层或掩膜层中非零体素的包围盒（含端点），并可选择把十字准线移到其中心。',
      'viewer.help.Overlay layer index; the base image is layer 0.': '叠加层序号；底图为第 0 层。',
      'viewer.help.Set or disable the 3D clip plane using NiiVue depth/azimuth/elevation parameters.': '用 NiiVue 的深度、方位角、俯仰角参数设置或关闭三维剖切面。',
      'viewer.help.Set a volume layer opacity or visible state. Layer 0 is the base image; overlays start at 1.': '设置某个体数据图层的不透明度或可见状态。第 0 层是底图，叠加层从 1 开始。',
      'viewer.help.Show or hide a whole loaded structure. Partial residue/atom visibility is intentionally unsupported.': '整体显示或隐藏一个已载入的结构。按残基或原子局部控制可见性是有意不支持的。',
      'viewer.help.Whole-structure selector (structure_id or structure_index only).': '整结构选择器（只接受 structure_id 或 structure_index）。',
      'viewer.help.Highlight a structure, chain, residue, or atom selection, or clear the current highlight.': '高亮某个结构、链、残基或原子选区，或清除当前高亮。',
      'viewer.help.Selection to highlight.': '要高亮的选区。',
      'viewer.help.Focus the Mol* camera on a structure, chain, residue, or atom selection.': '把 Mol* 相机聚焦到某个结构、链、残基或原子选区。',
      'viewer.help.Selection to focus.': '要聚焦的选区。',
      'viewer.help.Show exactly one loaded structure, hide the others, and focus the selected structure.': '只显示其中一个已载入的结构，隐藏其余结构，并聚焦到所选结构。',
      'viewer.help.Resolve two unambiguous atom selectors, draw a Mol* distance annotation, and return the distance in angstroms.': '解析两个无歧义的原子选择器，绘制 Mol* 距离标注，并返回以埃为单位的距离。',
      'viewer.help.First atom selector; it must resolve to exactly one displayed atom.': '第一个原子选择器，必须唯一命中一个已显示的原子。',
      'viewer.help.Second atom selector; it must resolve to exactly one displayed atom.': '第二个原子选择器，必须唯一命中一个已显示的原子。',
      'viewer.help.Restore the initial Mol* camera and structure visibility, and clear highlights.': '恢复 Mol* 的初始相机与结构可见性，并清除高亮。',
      'viewer.help.Restore the view and loaded-layer display state captured after initial load.': '恢复初次载入后记录的视图与各图层显示状态。',
      'viewer.help.Return camera state, stable structure IDs, visibility, and measurement count.': '返回相机状态、稳定的结构 ID、可见性与已有测量数量。',
      'viewer.help.Return the current NiiVue view, crosshair, layer, zoom, pan, and clip state.': '返回 NiiVue 当前的视图、十字准线、图层、缩放、平移与剖切状态。',

      // Wording Mol* draws itself. It ships no language layer, and its bundle is a
      // vendored release neither this repository nor the operator may edit, so the
      // workspace writes Chinese over the panel from outside the frame. Keyed by the
      // English text, so a label we have no wording for stays as Mol* drew it.
      'viewer.frame.Actions': '操作',
      'viewer.frame.Add': '添加',
      'viewer.frame.Add a new representation component for a selection.': '为选区新增一个表示组件。',
      'viewer.frame.Align Cartesian axes to the screen axes': '把笛卡尔坐标轴对齐到屏幕坐标轴',
      'viewer.frame.Align principal component axes of the loaded structures to the screen axes (“lay flat”)': '把已载入结构的主成分轴对齐到屏幕坐标轴（把结构放平）',
      'viewer.frame.All': '全部',
      'viewer.frame.Angle': '角度',
      'viewer.frame.Applies default representation preset and sets outline and occlusion effects to default': '套用默认表示预设，并把轮廓与遮蔽效果恢复为默认',
      'viewer.frame.Applies illustrative representation preset and Stylize it': '套用示意图表示预设，并加上风格化',
      'viewer.frame.Apply a representation preset for the current structure(s).': '为当前结构套用一个表示预设。',
      'viewer.frame.Apply a structure presets to the current hierarchy.': '为当前结构层级套用一个结构预设。',
      'viewer.frame.Assembly': '组装体',
      'viewer.frame.Ball & Stick': '球棍模型',
      'viewer.frame.Cartoon': '卡通模型',
      'viewer.frame.Chain': '链',
      'viewer.frame.Click to collapse': '点击折叠',
      'viewer.frame.Click to expand': '点击展开',
      'viewer.frame.Components': '组件',
      'viewer.frame.Crystal Contacts': '晶体接触',
      'viewer.frame.Default': '默认',
      'viewer.frame.Dihedral': '二面角',
      'viewer.frame.Distance': '距离',
      'viewer.frame.Does not change representation, enables outline and occlusion effects, enables ignore-light representation parameter': '不改变表示方式，只开启轮廓与遮蔽效果，并启用忽略光照参数',
      'viewer.frame.Everything': '全部内容',
      'viewer.frame.Export Animation': '导出动画',
      'viewer.frame.Export Geometry': '导出几何',
      'viewer.frame.Export Models': '导出模型',
      'viewer.frame.Help': '帮助',
      'viewer.frame.Hide component': '隐藏组件',
      'viewer.frame.Home': '回到初始视角',
      'viewer.frame.Illustrative': '示意图',
      'viewer.frame.Ion': '离子',
      'viewer.frame.Label': '标签',
      'viewer.frame.Ligand': '配体',
      'viewer.frame.Measurements': '测量',
      'viewer.frame.Model': '模型',
      'viewer.frame.Model Index': '模型序号',
      'viewer.frame.Nothing Focused': '未聚焦任何对象',
      'viewer.frame.Options': '选项',
      'viewer.frame.Options that are applied to all applicable representations.': '适用于所有相关表示方式的选项。',
      'viewer.frame.Orient Axes': '主轴摆正',
      'viewer.frame.Orientation': '取向',
      'viewer.frame.Plane': '平面',
      'viewer.frame.Plugin State': '插件状态',
      'viewer.frame.Polymer': '聚合物',
      'viewer.frame.Polymers': '聚合物',
      'viewer.frame.Preset': '预设',
      'viewer.frame.Quick Styles': '快捷样式',
      'viewer.frame.Remove': '移除',
      'viewer.frame.Remove All': '全部移除',
      'viewer.frame.Reset Axes': '重置坐标轴',
      'viewer.frame.Reset Zoom': '重置缩放',
      'viewer.frame.Screenshot / State Snapshot': '截图 / 状态快照',
      'viewer.frame.Select Animation': '选择动画',
      'viewer.frame.Select a focus target to center on an show its surroundings. Hold shift to focus on multiple targets.': '选择一个聚焦目标，把它居中并显示周围环境。按住 Shift 可同时聚焦多个目标。',
      'viewer.frame.Sequence of': '序列来自',
      'viewer.frame.Set camera zoom to fit the visible scene into view': '调整相机缩放，把可见场景完整装进画面',
      'viewer.frame.Settings': '设置',
      'viewer.frame.Settings / Controls Info': '设置 / 控件说明',
      'viewer.frame.Show': '显示',
      'viewer.frame.Some mistakes of the past can be undone.': '之前的部分操作可以撤销。',
      'viewer.frame.State Tree': '状态树',
      'viewer.frame.Structure': '结构',
      'viewer.frame.Structure Tools': '结构工具',
      'viewer.frame.Stylize Current': '风格化当前视图',
      'viewer.frame.Super Cell': '超胞',
      'viewer.frame.Symmetry Mates': '对称副本',
      'viewer.frame.This shows a single sequence. Use the controls to show a different sequence.': '这里只显示一条序列，用上方控件可切换到其他序列。',
      'viewer.frame.Toggle Controls Panel': '显示或隐藏控制面板',
      'viewer.frame.Toggle Expanded Viewport': '切换全屏视图',
      'viewer.frame.Toggle Selection Mode': '切换选择模式',
      'viewer.frame.Type': '类型',
      'viewer.frame.Type Properties': '类型属性',
      'viewer.frame.Unit Cell': '晶胞',
      'viewer.frame.Water': '水分子',

      // The viewer pages' own chrome. Keyed the same way rather than translated in
      // place, because online the pages come from the operator's registered viewer
      // tree and this repository only serves them.
      'viewer.frame.(no file)': '（无文件）',
      'viewer.frame.Axial': '轴位',
      'viewer.frame.Clear': '清除',
      'viewer.frame.Clear bounding-box endpoints': '清除包围盒端点',
      'viewer.frame.Set A': '设为 A',
      'viewer.frame.Set B': '设为 B',
      'viewer.frame.Set the first bounding-box endpoint': '设置包围盒的第一个端点',
      'viewer.frame.Set the second bounding-box endpoint': '设置包围盒的第二个端点',
      'viewer.frame.axial / coronal / sagittal triplanar': '轴位 / 冠状位 / 矢状位 三平面',
      'viewer.frame.axial slice only': '仅轴位断层',
      'viewer.frame.drag = pan · wheel = slice · right-drag = window/level': '拖拽 = 平移 · 滚轮 = 换断层 · 右键拖拽 = 调窗',
      'viewer.frame.drag = rotate · wheel = clip · right-drag = pan': '拖拽 = 旋转 · 滚轮 = 剖切 · 右键拖拽 = 平移',
      'viewer.frame.full-screen 3D volume render': '全屏三维体渲染',
      'viewer.frame.← Shell': '← 返回查看器首页',
    },
    en: {
      'lang.switchTo': '中文',
      'lang.switchTitle': 'Switch to the Chinese interface',
      'login.documentTitle': 'ScienceVision Sign In',
      'login.heading': 'ScienceVision Review',
      'login.intro':
        'Assigned 3D tasks, validation feedback, answers, and reasoning in one workspace.',
      'login.facet.viewer.term': 'Viewer',
      'login.facet.viewer.desc': 'Task-specific 3D viewers',
      'login.facet.drafts.term': 'Drafts',
      'login.facet.drafts.desc': 'Saved in this browser',
      'login.facet.submission.term': 'Submission',
      'login.facet.submission.desc': 'Validated and stored centrally',
      'login.secureAccess': 'Secure access',
      'login.signInHeading': 'Sign in',
      'login.signInHint': 'Use the credentials assigned to your expert profile.',
      'login.usernameLabel': 'Username',
      'login.passwordLabel': 'Password',
      'login.submit': 'Continue',
      'login.activationEyebrow': 'Account activation',
      'login.activationHeading': 'Create your password',
      'login.activationHint':
        'Your temporary password cannot open assigned tasks. Choose a private password to continue.',
      'login.newPasswordLabel': 'New password',
      'login.confirmPasswordLabel': 'Confirm new password',
      'login.activateSubmit': 'Activate account',
      'login.signOut': 'Sign out',
      'login.status.signingIn': 'Signing in...',
      'login.status.activating': 'Activating account...',
      'login.error.signInFailed': 'Sign in failed',
      'login.error.activationFailed': 'Account activation failed',
      'login.error.sessionLoad': 'Password activation session could not be loaded',
      'expert.documentTitle': 'ScienceVision Expert Review',
      'expert.brand': 'ScienceVision Expert Review',
      'expert.packageLoading': 'Loading package...',
      'expert.focusViewer': 'Focus viewer',
      'expert.focusViewerTitle': 'Give the 3D viewer the full workspace',
      'expert.showTasks': 'Show tasks & response',
      'expert.showTasksTitle': 'Show assigned tasks and response fields',
      'expert.exportResults': 'Export results',
      'expert.submitResults': 'Submit this task',
      'expert.saveDraft': 'Save draft',
      'expert.status.savedServer': 'Saved on the server',
      'expert.signOut': 'Sign out',
      'expert.tasksRailLabel': 'Assigned tasks',
      'expert.tasksLabel': 'Tasks',
      'expert.filter.all': 'All',
      'expert.filter.complete': 'Complete',
      'expert.filter.incomplete': 'Incomplete',
      'expert.selectTask': 'Select a task',
      'expert.validationLabel': 'Task validation',
      'expert.validation.uncertain': 'Uncertain',
      'expert.validation.valid': 'Valid',
      'expert.validation.invalid': 'Invalid',
      'expert.validationFeedbackLabel': 'Validation feedback',
      'expert.answerLabel': 'Answer',
      'expert.reasoningLabel': 'Reasoning',
      'expert.steps.legend': 'Key operations',
      'expert.steps.hint': 'Record only the operations that changed your judgement, and say why each one mattered; a log of every mouse move is not wanted.',
      'expert.steps.actionLabel': 'Operation',
      'expert.steps.action.zoomIn': 'Zoom in',
      'expert.steps.action.zoomOut': 'Zoom out',
      'expert.steps.action.rotate': 'Rotate',
      'expert.steps.action.pan': 'Pan',
      'expert.steps.action.slice': 'Move slice',
      'expert.steps.action.measure': 'Measure',
      'expert.steps.action.isolate': 'Isolate structure',
      'expert.steps.action.resetView': 'Reset view',
      'expert.steps.action.screenshot': 'Screenshot',
      'expert.steps.action.other': 'Other',
      'expert.steps.noteLabel': 'Why it mattered',
      'expert.steps.notePlaceholder': 'For example: one zoom was not enough to read the ligand, so I zoomed in to atom level',
      'expert.steps.add': 'Record step',
      'expert.steps.remove': 'Remove',
      'expert.steps.removeTitle': 'Remove this step',
      'expert.steps.noteRequired': 'Say why this operation mattered',
      'expert.steps.recorded': 'Key operation recorded',
      'expert.steps.removed': 'Key operation removed',
      'expert.steps.tooMany': 'Keep at most {limit} key operations',
      'expert.attemptLabel': 'Attempt',
      'expert.viewerTitle': 'ScienceVision 3D viewer',
      'expert.viewerBarTitle': '3D Viewer',
      'expert.unclassified': 'unclassified',
      'expert.progress': '{complete} / {total} complete',
      'expert.status.attemptInvalid': 'Attempt must be 1 or greater',
      'expert.status.savedLocally': 'Saved locally',
      'expert.status.localSaveFailed': 'Local save failed',
      'expert.status.alreadySubmitted': 'Already submitted',
      'expert.status.submitted': 'Submitted {count} result(s)',
      'expert.error.draftSave': 'Draft save failed: {message}',
      'expert.error.draftLoad': 'Saved drafts could not be loaded: {message}',
      'expert.error.taskOpenHttp': 'Task could not be opened (HTTP {status})',
      'expert.error.taskOpen': 'Task could not be opened',
      'expert.error.taskOpenInvalid': 'Task open response was invalid',
      'expert.error.taskSelect': 'Task selection failed: {message}',
      'expert.error.incompleteTasks': '{count} task(s) still need validation feedback, an answer, and reasoning.',
      'expert.error.incompleteCurrentTask': 'This task still needs validation feedback, an answer, and reasoning.',
      'expert.error.trajectoryUnavailable': 'Trajectory capture unavailable: {message}',
      'expert.error.submissionFailed': 'Submission failed',
      'expert.error.manifestLoad': 'Manifest load failed: HTTP {status}',
      'expert.error.bundleManifest': 'Expert bundle manifest could not be loaded',
      'expert.error.bootstrap': 'Active task bundle could not be loaded',
      'expert.error.tasksLoad': 'Assigned tasks could not be loaded',
      'expert.error.taskCountMismatch': 'Task count mismatch',
      'expert.guide.summary': 'How this page works',
      'expert.guide.flow': 'End-to-end flow: pick a task on the left, inspect it in the 3D viewer and capture evidence, judge whether the task is valid, write the answer and the reasoning, then submit this task from the top right.',
      'expert.guide.s1.title': '01 Pick a task',
      'expert.guide.s1.io': 'Input: the Tasks list on the left, narrowed with the Incomplete filter → Output: the question, its instruction and its 3D scene on the right',
      'expert.guide.s2.title': '02 Inspect the scene',
      'expert.guide.s2.io': 'Input: drag to orbit and scroll to zoom in the 3D viewer; Focus viewer at the top right hands it the whole workspace → Output: a clear view of the structure the task asks about',
      'expert.guide.s3.title': '03 Capture a screenshot',
      'expert.guide.s3.io': 'Input: the Screenshot button on the viewer toolbar, then drag on the preview to select an area → Output: Copy selection puts it on the clipboard, Download PNG saves the image evidence plus a JSON file of the same name recording the task, the crop and the view it was taken from',
      'expert.guide.s4.title': '04 Judge the task',
      'expert.guide.s4.io': 'Input: set Task validation to valid, invalid or uncertain and give the reason in Validation feedback → Output: a quality verdict on the task itself, which the admin uses to repair it',
      'expert.guide.s5.title': '05 Answer',
      'expert.guide.s5.io': 'Input: the conclusion in Answer, the decisive evidence in Reasoning, and which run this is in Attempt → Output: every edit is saved locally, with the status shown under the form',
      'expert.guide.s6.title': '06 Submit',
      'expert.guide.s6.io': 'Input: Submit this task online, or Export results for an offline bundle, from the top right → Output: this task reaches the admin side and waits for review; other tasks can still be answered',
      'expert.guide.cameraNote': 'The three camera buttons at the top right of the viewer come from Mol* itself: Reset Zoom only changes the distance, refitting the whole structure into view; Orient Axes lays the structure flat along its own principal axes; Reset Axes returns to the X/Y/Z directions of the coordinate system. The last two differ in what they align to, the shape of the molecule or the Cartesian axes.',
      'expert.guide.measureNote': 'To measure a distance between atoms: click Selection Mode (Toggle Selection Mode) at the top right of the viewer, click the two atoms in turn, then open the left panel and go to Structure Tools → Measurements → Distance. The distance is labelled in the scene in Å. Click Selection Mode again to leave it, so later clicks do not keep adding to the selection.',
      'expert.guide.symmetryNote': 'A structure file often carries only its asymmetric unit, so periodic image molecules are absent from the scene. To bring them in: open the left panel, use the structure preset button in the Structure section and pick Crystal Contacts (neighbouring chains within 5 Å, the closest match for a nearest-contact question), Unit Cell (fills the whole cell) or Super Cell. You can also switch the structure Type from Model to Symmetry Mates and set its radius. These entries appear only for files that carry crystal symmetry: if none of them is offered, the file has none, so mark the task invalid and say why.',
      'expert.guide.reasoningNote': 'Reasoning records the semantically key steps only: the operation that actually changed your judgement (one zoom was not enough, so you kept zooming to the atom level). It is not a log of every mouse move. Saving a step in the browser MCP panel is one of those key operations by definition, so every saved step needs its note before the trajectory can be submitted.',
      'expert.guide.next': 'Next: go back to the list on the left and take the next task; finished ones are marked in the list.',
      'capture.viewerTitle': '3D Viewer',
      'capture.shot': 'Screenshot',
      'capture.shotCapturing': 'Capturing...',
      'capture.shotTitle': 'Capture the complete 3D viewer',
      'capture.shotUnavailable': 'Screenshot is unavailable',
      'capture.dialogTitle': 'Crop screenshot',
      'capture.closeTitle': 'Close screenshot editor',
      'capture.reset': 'Reset selection',
      'capture.resetTitle': 'Reset crop to the complete image',
      'capture.copy': 'Copy selection',
      'capture.copyTitle': 'Copy the selected area as PNG',
      'capture.copyUnsupported': 'PNG clipboard copy is not supported in this browser context',
      'capture.download': 'Download PNG',
      'capture.downloadTitle': 'Download the selected area as PNG',
      'capture.canvasLabel': 'Screenshot crop preview; drag to select an area',
      'capture.canvasSelected': 'Screenshot crop preview. Selected {selectionWidth} by {selectionHeight} pixels at {x}, {y}. Drag to select an area.',
      'capture.meta': 'Image {width} x {height} px | Selection {selectionWidth} x {selectionHeight} px at {x}, {y}',
      'capture.status.viewerLoading': 'Viewer loading...',
      'capture.status.noViewer': 'No viewer selected',
      'capture.status.notReady': 'Viewer screenshot is not ready',
      'capture.status.capturingFull': 'Capturing full viewer...',
      'capture.status.captured': 'Screenshot captured',
      'capture.status.clipboardUnavailable': 'Clipboard PNG copy is unavailable; download remains available.',
      'capture.status.selectionTooSmall': 'Selection was too small; the previous selection was kept.',
      'capture.status.copying': 'Copying selection...',
      'capture.status.copied': 'Selection copied to clipboard.',
      'capture.status.preparing': 'Preparing PNG...',
      'capture.status.downloadStarted': 'PNG and view state download started.',
      'capture.view.recorded': 'View state will be saved next to the PNG',
      'capture.view.unavailable': 'This viewer does not report a view state',
      'capture.error.copyFailed': 'Copy failed: {message}',
      'capture.error.downloadFailed': 'Download failed: {message}',
      'capture.error.unknown': 'Unknown screenshot error.',
      'capture.error.decode': 'The screenshot PNG could not be decoded.',
      'capture.error.dimensions': 'The screenshot has invalid dimensions.',
      'capture.error.gone': 'Screenshot is no longer available.',
      'capture.error.canvasUnavailable': 'This browser cannot create a crop canvas.',
      'capture.error.encode': 'The selected area could not be encoded as PNG.',
      'manager.guide.summary': 'How this page works',
      'manager.guide.flow': 'End-to-end flow: import the expert roster, import a task batch, assign tasks, publish, the expert sees the assigned tasks in their own workspace, then import the returned results.',
      'manager.guide.setup.s1.title': '01 Import experts',
      'manager.guide.setup.s1.io': 'Input: expert roster CSV (expert_id, name, domains) → Output: expert profiles that can receive assignments',
      'manager.guide.setup.s2.title': '02 Import a task batch',
      'manager.guide.setup.s2.io': 'Input: batch id, title, task JSONL, asset root, admission receipt → Output: an admitted batch and its assignable tasks',
      'manager.guide.setup.s3.title': '03 Invite an expert account',
      'manager.guide.setup.s3.io': 'Input: press Invite on a row of the experts table below → Output: a one-time temporary password the expert must replace at first login',
      'manager.guide.setup.next': 'Next: open Distribution and hand the batch tasks to experts.',
      'manager.guide.distribution.s1.title': '01 Balance automatically',
      'manager.guide.distribution.s1.io': 'Input: pick a batch → Output: assignments balanced across the domains of each expert',
      'manager.guide.distribution.s2.title': '02 Assign explicitly',
      'manager.guide.distribution.s2.io': 'Input: one unassigned task plus one expert → Output: a single targeted assignment',
      'manager.guide.distribution.s3.title': '03 Publish to every expert',
      'manager.guide.distribution.s3.io': 'Input: pick a batch → Output: the active expert bundles after an atomic switch; only a published batch is visible when an expert signs in',
      'manager.guide.distribution.s4.title': '04 Build an offline delivery package',
      'manager.guide.distribution.s4.io': 'Input: batch, expert, output path and package mode → Output: an offline task package on disk',
      'manager.guide.distribution.s5.title': '05 Build the shared runtime',
      'manager.guide.distribution.s5.io': 'Input: viewer root and output path → Output: a viewer runtime that many thin packages can reuse',
      'manager.guide.distribution.s6.title': '06 Import a historical package',
      'manager.guide.distribution.s6.io': 'Input: the package root, plus whether to activate it and whether missing checksums are allowed → Output: a historical package registered in the manager',
      'manager.guide.distribution.review': 'Review: rows in the Assignments table below whose status is pending_review carry Approve and Return buttons. A return needs a reason and sends the task back to that expert. Use the search box to sample by expert, status, batch or domain.',
      'manager.guide.distribution.next': 'Next: once experts submit, open Results to import and export.',
      'manager.guide.results.s1.title': '01 Import results',
      'manager.guide.results.s1.io': 'Input: the submission JSON an expert exported → Output: validated result records in the store',
      'manager.guide.results.s2.title': '02 Export results',
      'manager.guide.results.s2.io': 'Input: choose JSON or CSV → Output: one file covering every accepted result',
      'manager.guide.results.next': 'Next: check the Imported results table below; send anything that needs rework back through Distribution.',
      'manager.error.request': 'Request failed',
      'manager.error.accountUpdate': 'Account update failed',
      'manager.error.stateLoad': 'Manager state could not be loaded',
      'manager.error.reviewBundlesLoad': 'Review bundles could not be loaded',
      'manager.error.accountsLoad': 'Accounts could not be loaded',
      'manager.status.refreshed': 'Manager state refreshed.',
      'manager.summary.coverage': '{percent}% coverage',
      'manager.account.status.notInvited': 'Not invited',
      'manager.account.status.activationPending': 'Activation pending',
      'manager.account.status.active': 'Active',
      'manager.account.status.offlineOnly': 'Offline only',
      'manager.account.invite': 'Invite',
      'manager.account.reissue': 'Reissue temporary password',
      'manager.account.reissueConfirm': 'Reissue this expert password? Existing sessions will be revoked.',
      'manager.account.inviting': 'Creating invitation...',
      'manager.account.reissuing': 'Reissuing password...',
      'manager.account.created': 'Temporary password created. It is shown only in the secure panel above.',
      'manager.credential.durabilityWarning': 'The account was updated, but directory durability could not be confirmed. Record this credential and verify persistent storage before relying on it.',
      'manager.assignment.approve': 'Approve',
      'manager.assignment.return': 'Return',
      'manager.assignment.returnReasonPrompt': 'Reason for returning this assignment:',
      'manager.assignment.returnReasonRequired': 'A return reason is required.',
      'manager.assignment.approveConfirm': 'Approve assignment {assignment}?',
      'manager.reviewBundle.type': 'Checksummed static bundle',
      'manager.reviewBundle.meta': '{count} tasks · Not imported / read-only',
      'manager.reviewBundle.policy': 'Local drafts and export only. Central submit unavailable.',
      'manager.reviewBundle.open': 'Open viewer',
      'manager.reviewBundle.replay': 'Replay trajectory',
      'manager.accounts.reset': 'Reset password',
      'manager.accounts.notResettable': 'Not resettable here',
      'manager.reset.failed': 'Password reset failed',
      'manager.reset.incomplete': 'Password reset response was incomplete',
      'manager.reset.durabilityPending': 'Password is active, but durable storage confirmation is pending.',
      'manager.reset.clipboardUnavailable': 'Clipboard access is unavailable.',
      'manager.reset.copied': 'Copied.',
      'manager.reset.copyFailed': 'Copy failed.',
      'manager.publish.validating': 'Validating every expert bundle before activation...',
      'manager.publish.success': '{count} expert bundle(s) activated atomically.',
      'manager.publish.failed': 'Publish failed. Active bundles were not changed. {error}',
      'manager.publish.refreshFailed': 'The publish succeeded, but status refresh failed; use Refresh.',
      'manager.brandTag': 'Task operations',
      'manager.navLabel': 'Manager views',
      'manager.nav.workflow': 'Workflow',
      'manager.nav.setup': 'Setup',
      'manager.nav.setupHint': 'People and batches',
      'manager.nav.distribution': 'Distribution',
      'manager.nav.distributionHint': 'Assignments and packages',
      'manager.nav.results': 'Results',
      'manager.nav.resultsHint': 'Intake and exports',
      'manager.status.online': 'Manager online',
      'manager.status.onlineHint': 'Authenticated workspace',
      'manager.adminDefault': 'Administrator',
      'manager.adminAccess': 'Admin access',
      'manager.signOut': 'Sign out',
      'manager.eyebrow': 'Admin workspace',
      'manager.refresh': 'Refresh',
      'manager.refreshTitle': 'Refresh manager state',
      'manager.view.setup.title': 'Setup',
      'manager.view.setup.description': 'Experts and source batches',
      'manager.view.distribution.title': 'Distribution',
      'manager.view.distribution.description': 'Assignments and expert packages',
      'manager.view.results.title': 'Results',
      'manager.view.results.description': 'Validated intake and exports',
      'manager.summaryLabel': 'Workflow summary',
      'manager.summary.experts': 'Experts',
      'manager.summary.profiles': 'Profiles',
      'manager.summary.batches': 'Batches',
      'manager.summary.sources': 'Sources',
      'manager.summary.tasks': 'Tasks',
      'manager.summary.imported': 'Imported',
      'manager.summary.assignments': 'Assignments',
      'manager.summary.bundles': 'Active bundles',
      'manager.summary.published': 'Published',
      'manager.summary.results': 'Results',
      'manager.summary.accepted': 'Accepted',
      'manager.activityLabel': 'Latest manager activity',
      'manager.activity': 'Activity',
      'manager.activityReady': 'Ready.',
      'manager.setup.eyebrow': 'Foundation',
      'manager.setup.heading': 'Experts and task sources',
      'manager.setup.note': 'Roster and batch inventory',
      'manager.reviewBundles.eyebrow': 'Administrator review',
      'manager.reviewBundles.heading': 'Review bundles',
      'manager.reviewBundles.note': 'Verified local sources outside the manager database',
      'manager.accounts.heading': 'Online accounts',
      'manager.accounts.note': 'Administrator and expert sign-in accounts',
      'manager.accounts.col.name': 'Name',
      'manager.accounts.col.username': 'Username',
      'manager.accounts.col.role': 'Role',
      'manager.accounts.col.expertId': 'Expert ID',
      'manager.accounts.col.access': 'Access',
      'manager.expertImport.heading': 'Import experts',
      'manager.expertImport.note': 'Expert roster',
      'manager.expertImport.fileLabel': 'Experts CSV',
      'manager.expertImport.csvLabel': 'CSV content',
      'manager.expertImport.submit': 'Import experts',
      'manager.batchImport.heading': 'Import task batch',
      'manager.batchImport.note': 'Validated source material',
      'manager.batchImport.batchId': 'Batch ID',
      'manager.batchImport.title': 'Title',
      'manager.batchImport.tasksJsonl': 'Tasks JSONL path',
      'manager.batchImport.assetRoot': 'Asset root',
      'manager.batchImport.receipt': 'QC admission receipt',
      'manager.batchImport.purpose': 'Admission purpose',
      'manager.batchImport.purposeReview': 'Expert review distribution',
      'manager.batchImport.viewerRoot': 'Viewer root',
      'manager.batchImport.submit': 'Preflight and import',
      'manager.credential.label': 'One-time expert credential',
      'manager.credential.warning': 'Share this temporary password securely. It will not be shown again.',
      'manager.credential.dismiss': 'Dismiss',
      'manager.expertsPanel.heading': 'Experts',
      'manager.expertsPanel.countSuffix': 'profiles',
      'manager.expertsPanel.filter': 'Filter experts',
      'manager.expertsPanel.col.expertId': 'Expert ID',
      'manager.expertsPanel.col.name': 'Name',
      'manager.expertsPanel.col.domains': 'Domains',
      'manager.expertsPanel.col.account': 'Account',
      'manager.expertsPanel.col.action': 'Action',
      'manager.expertsPanel.col.updated': 'Updated',
      'manager.batchesPanel.heading': 'Batches',
      'manager.batchesPanel.countSuffix': 'sources',
      'manager.batchesPanel.filter': 'Filter batches',
      'manager.batchesPanel.col.batchId': 'Batch ID',
      'manager.batchesPanel.col.title': 'Title',
      'manager.batchesPanel.col.tasks': 'Tasks',
      'manager.batchesPanel.col.assigned': 'Assigned',
      'manager.batchesPanel.col.imported': 'Imported',
      'manager.distribution.eyebrow': 'Operations',
      'manager.distribution.heading': 'Assignment and packaging',
      'manager.distribution.note': 'Domain-balanced task distribution',
      'manager.auto.heading': 'Auto-balance',
      'manager.auto.note': 'By expert domain',
      'manager.auto.batch': 'Batch',
      'manager.auto.submit': 'Assign unassigned tasks',
      'manager.explicit.heading': 'Explicit assignment',
      'manager.explicit.note': 'One task to one expert',
      'manager.explicit.task': 'Unassigned task',
      'manager.explicit.expert': 'Expert',
      'manager.explicit.submit': 'Assign task',
      'manager.publish.heading': 'Publish to all experts',
      'manager.publish.note': 'Atomic active-bundle switch',
      'manager.publish.batch': 'Fully assigned batch',
      'manager.publish.idle': 'No publish run yet.',
      'manager.publish.submit': 'Publish all expert bundles',
      'manager.package.heading': 'Build offline delivery',
      'manager.package.note': 'Thin or standalone',
      'manager.package.batch': 'Batch',
      'manager.package.expert': 'Expert',
      'manager.package.mode': 'Delivery mode',
      'manager.package.modeThin': 'Thin bundle',
      'manager.package.modeStandalone': 'Standalone package',
      'manager.package.output': 'New output directory',
      'manager.package.submit': 'Build offline delivery',
      'manager.runtime.heading': 'Build shared runtime',
      'manager.runtime.note': 'Install once for thin bundles',
      'manager.runtime.viewerRoot': 'Viewer source directory',
      'manager.runtime.output': 'New runtime directory',
      'manager.runtime.submit': 'Build shared runtime',
      'manager.legacy.heading': 'Import legacy package',
      'manager.legacy.note': 'Explicit pre-admission adapter',
      'manager.legacy.root': 'Existing v1 package directory',
      'manager.legacy.activate': 'Make active for its expert',
      'manager.legacy.missingChecksums': 'Audited historical package without SHA256SUMS',
      'manager.legacy.submit': 'Import legacy package',
      'manager.assignmentsPanel.heading': 'Assignments',
      'manager.assignmentsPanel.countSuffix': 'task owners',
      'manager.assignmentsPanel.filter': 'Filter assignments',
      'manager.assignmentsPanel.col.batch': 'Batch',
      'manager.assignmentsPanel.col.task': 'Task',
      'manager.assignmentsPanel.col.domain': 'Domain',
      'manager.assignmentsPanel.col.expert': 'Expert',
      'manager.assignmentsPanel.col.status': 'Status',
      'manager.assignmentsPanel.col.assignmentId': 'Assignment ID',
      'manager.assignmentsPanel.col.review': 'Review',
      'manager.assignmentsPanel.filterBatch': 'Batch',
      'manager.assignmentsPanel.filterExpert': 'Expert',
      'manager.assignmentsPanel.filterStatus': 'Status',
      'manager.assignmentsPanel.filterDomain': 'Domain',
      'manager.assignmentsPanel.filterAll': 'All',
      'manager.assignmentsPanel.filterActive': 'Showing {shown} of {total} assignments',
      'manager.assignmentsPanel.sampleSize': 'Sample size',
      'manager.assignmentsPanel.sample': 'Draw sample',
      'manager.assignmentsPanel.sampleClear': 'Clear sample',
      'manager.assignmentsPanel.sampleActive': 'Spot-checking {shown} of {total} assignments. The sample only changes this view; review decisions are still written to the audit log.',
      'manager.assignmentsPanel.sampleEmpty': 'No assignments match the current filters.',
      'manager.activeBundlesPanel.heading': 'Active expert bundles',
      'manager.activeBundlesPanel.countSuffix': 'experts ready to review',
      'manager.activeBundlesPanel.filter': 'Filter active bundles',
      'manager.activeBundlesPanel.col.expert': 'Expert',
      'manager.activeBundlesPanel.col.batch': 'Batch',
      'manager.activeBundlesPanel.col.bundle': 'Bundle',
      'manager.activeBundlesPanel.col.tasks': 'Tasks',
      'manager.activeBundlesPanel.col.admission': 'Admission',
      'manager.activeBundlesPanel.col.activated': 'Activated',
      'manager.packagesPanel.heading': 'Standalone package history',
      'manager.packagesPanel.countSuffix': 'registered v1 packages',
      'manager.packagesPanel.filter': 'Filter packages',
      'manager.packagesPanel.col.batch': 'Batch',
      'manager.packagesPanel.col.expert': 'Expert',
      'manager.packagesPanel.col.assignments': 'Assignments',
      'manager.packagesPanel.col.packageId': 'Package ID',
      'manager.packagesPanel.col.created': 'Created',
      'manager.results.eyebrow': 'Completion',
      'manager.results.heading': 'Result intake and exports',
      'manager.results.note': 'Validated expert submissions',
      'manager.resultsImport.heading': 'Import results',
      'manager.resultsImport.note': 'Expert submission JSON',
      'manager.resultsImport.fileLabel': 'Submission JSON',
      'manager.resultsImport.submit': 'Validate and import',
      'manager.resultsExport.heading': 'Export results',
      'manager.resultsExport.note': 'Consolidated output',
      'manager.resultsPanel.heading': 'Imported results',
      'manager.resultsPanel.countSuffix': 'accepted rows',
      'manager.resultsPanel.filter': 'Filter results',
      'manager.resultsPanel.col.task': 'Task',
      'manager.resultsPanel.col.expert': 'Expert',
      'manager.resultsPanel.col.validation': 'Validation',
      'manager.resultsPanel.col.feedback': 'Feedback',
      'manager.resultsPanel.col.answer': 'Answer',
      'manager.resultsPanel.col.reasoning': 'Reasoning',
      'manager.resultsPanel.col.trajectory': 'Trajectory',
      'manager.resultsPanel.steps.summary': '{count} key operations',
      'manager.reset.eyebrow': 'Account access',
      'manager.reset.confirmHeading': 'Reissue temporary password',
      'manager.reset.note': 'The server generates the password, shows it once, and the expert must replace it at first login. Existing sessions are revoked.',
      'manager.reset.cancel': 'Cancel',
      'manager.reset.confirm': 'Reissue password',
      'manager.reset.secretHeading': 'New password',
      'manager.reset.passwordLabel': 'Password',
      'manager.reset.reveal': 'Reveal',
      'manager.reset.hide': 'Hide',
      'manager.reset.clearOnClose': 'Closing this dialog clears the password from this workspace.',
      'manager.reset.copy': 'Copy',
      'manager.reset.close': 'Close',
      'mcp.panel.title': 'Record GT',
      'mcp.panel.function': 'Function',
      'mcp.panel.exploration': 'Show non-GT exploration tools',
      'mcp.panel.run': 'Run preview',
      'mcp.panel.running': 'Running...',
      'mcp.panel.runHint': 'Reset the viewer, replay the saved GT steps, then execute the selected function. Nothing is saved until you press Save step.',
      'mcp.panel.rawReceipt': 'Raw receipt',
      'mcp.panel.resultLabel': 'Viewer function result',
      'mcp.panel.tab.functions': 'Functions',
      'mcp.panel.tab.trajectory': 'Trajectory',
      'mcp.result.empty': 'No result.',
      'mcp.tool.waiting': 'Waiting for viewer',
      'mcp.tool.none': 'No viewer selected',
      'mcp.tool.noFunctions': 'No viewer functions',
      'mcp.tool.noCompliant': 'No task-compliant functions',
      'mcp.tool.marker.gt': '[GT action] ',
      'mcp.tool.marker.exploration': '[Non-GT exploration] ',
      'mcp.tool.marker.utility': '[Non-GT utility] ',
      'mcp.tool.policy.gt': '[Ground Truth action | savable] ',
      'mcp.tool.policy.exploration': '[Non-GT exploration | cannot save] ',
      'mcp.tool.policy.utility': '[Non-GT utility | cannot save] ',
      'mcp.field.include': 'include',
      'mcp.field.arrayItem': '{name} {index}',
      'mcp.availability.waiting': 'Waiting for viewer tool compatibility...',
      'mcp.availability.noTask': 'Select a task to inspect Ground Truth actions.',
      'mcp.availability.blocked': 'Blocked: this task allows no function exposed by the current viewer. Non-GT tools can be previewed, but no step can be saved.',
      'mcp.availability.ready.one': '{count} Ground Truth action available. Utility and exploration actions are Non-GT and cannot be saved.',
      'mcp.availability.ready.many': '{count} Ground Truth actions available. Utility and exploration actions are Non-GT and cannot be saved.',
      'mcp.status.noViewer': 'No viewer selected',
      'mcp.status.loading': 'Loading {viewer} functions...',
      'mcp.status.counts': '{gt} GT / {utility} utility / {exploration} exploration',
      'mcp.status.running': '{prefix}{name} running...',
      'mcp.status.previewStage': 'Preview / {label}...',
      'mcp.status.succeeded': '{prefix}{name} succeeded',
      'mcp.stage.replay': 'replaying saved GT steps',
      'mcp.stage.executing': 'executing {name}',
      'mcp.stage.capturing': 'capturing result state',
      'mcp.feedback.idle': 'Pick a function, then press Run preview.',
      'mcp.feedback.idleNoGT': 'Pick a function to preview. No function here can be saved as GT.',
      'mcp.feedback.waitingViewer': 'Waiting for the viewer to report its functions...',
      'mcp.feedback.noTask': 'Select a task to inspect Ground Truth actions.',
      'mcp.feedback.starting': '{name}: starting (reset -> replay -> execute)...',
      'mcp.feedback.stage': '{name}: {label}...',
      'mcp.feedback.saveHint': '{name} ran. Press Save step to record it as Ground Truth.',
      'mcp.feedback.explorationOnly': '{name} ran (exploration only - this step cannot be saved).',
      'mcp.feedback.error': '{name}: {message}',
      'trajectory.phase.review': 'Review',
      'trajectory.phase.trajectory': 'Trajectory',
      'trajectory.pendingAction.label': 'Ground-truth preview action',
      'trajectory.pending.none': 'No previews',
      'trajectory.pending.uncommitted': 'Save was not committed. The selected action and screenshot are retained; retry without repeating your work.',
      'trajectory.pending.one': '{count} preview since last screenshot',
      'trajectory.pending.many': '{count} previews since last screenshot',
      'trajectory.pending.prompt': 'Preview a Ground Truth action, then save it with a screenshot.',
      'trajectory.capture.save': 'Save step + screenshot',
      'trajectory.capture.retrySave': 'Retry save',
      'trajectory.capture.retryCapture': 'Retry capture + save',
      'trajectory.final.label': 'Final result',
      'trajectory.action.export': 'Export draft',
      'trajectory.action.submit': 'Submit trajectory',
      'trajectory.action.submitUnconfigured': 'Submit unconfigured',
      'trajectory.action.reset': 'Reset all',
      'trajectory.submit.title': 'Submit the trajectory to the configured backend',
      'trajectory.submit.titleUnconfigured': 'Submission unconfigured: host must inject the trajectory endpoint meta tag.',
      'trajectory.steps.heading': 'Saved Ground Truth steps',
      'trajectory.steps.empty': 'No saved steps.',
      'trajectory.delivery.initial': 'Export available / Submit not configured',
      'trajectory.delivery.summary': '{saved} saved / {final} / export ready / {submit}',
      'trajectory.delivery.finalReady': 'final ready',
      'trajectory.delivery.finalMissing': 'final missing',
      'trajectory.delivery.submitted': 'submitted',
      'trajectory.delivery.submitReady': 'submit ready',
      'trajectory.delivery.submitUnavailable': 'submit unavailable',
      'trajectory.step.title': 'Step {step}',
      'trajectory.step.delete': 'Delete',
      'trajectory.step.deleteLabel': 'Delete step {step}',
      'trajectory.step.screenshotAlt': 'Step {step} viewer screenshot',
      'trajectory.step.action': 'Ground-truth action',
      'trajectory.step.actionBound': 'The action is bound to this screenshot.',
      'trajectory.step.inputLabel': 'Step {step} function input',
      'trajectory.step.note': 'Step note / COT',
      'trajectory.step.noteRequired': 'Step note / COT (required)',
      'trajectory.status.noTask': 'No task selected',
      'trajectory.status.recovering': 'Recovering viewer from saved trajectory...',
      'trajectory.status.waitingViewer': 'Waiting for the new viewer document...',
      'trajectory.status.storageUnavailable': 'Draft storage unavailable: {detail}',
      'trajectory.status.recoveryFailed': 'Viewer recovery failed: {detail}',
      'trajectory.status.locked': 'Annotation locked: {detail}',
      'trajectory.status.lockedRollback': 'Annotation locked after rollback failure: {detail}',
      'trajectory.status.selectAction': 'Preview and select a Ground Truth action before saving.',
      'trajectory.status.saveStage': 'Save / {label}...',
      'trajectory.stage.replay': 'replaying saved GT steps',
      'trajectory.stage.executing': 'executing {name}',
      'trajectory.stage.capturing': 'capturing screenshot',
      'trajectory.stage.committing': 'committing screenshot and draft',
      'trajectory.status.saveFailed': 'Save was not committed: {detail}. Retry; the selected action is retained.',
      'trajectory.status.saveFailedWithCapture': 'Save was not committed: {detail}. Retry; the selected action and screenshot are retained.',
      'trajectory.status.stepSaved': 'Step {step} saved with screenshot',
      'trajectory.status.submitted.one': 'Submitted {count} step',
      'trajectory.status.submitted.many': 'Submitted {count} steps',
      'trajectory.status.saved.one': 'Draft saved / {count} step',
      'trajectory.status.saved.many': 'Draft saved / {count} steps',
      'trajectory.status.exported': 'Draft exported',
      'trajectory.status.needStep': 'Save at least one step before submitting.',
      'trajectory.status.needNote': 'Step {step} still needs the reason it mattered.',
      'trajectory.status.needFinal': 'Final result is required before submitting.',
      'trajectory.status.noTransport': 'Submission transport is not configured.',
      'trajectory.status.submitAcknowledged': 'Trajectory submission acknowledged',
      'trajectory.confirm.reset': 'Clear every saved step, pending preview, and reset the viewer?',
    },
  };

  const TOGGLE_STYLE = `
.sv-lang-toggle {
  position: fixed;
  top: 14px;
  right: 14px;
  z-index: 60;
  min-width: 52px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  line-height: 1.4;
  letter-spacing: .04em;
  opacity: .62;
  cursor: pointer;
}
.sv-lang-toggle:hover,
.sv-lang-toggle:focus-visible { opacity: 1; }
.sv-lang-toggle.sv-lang-toggle-inline {
  position: static;
  top: auto;
  right: auto;
  flex: none;
}
`;

// Shells that already own their top-right corner take the switch into the row,
// because a viewport-pinned button lands on the controls sitting there.
const TOGGLE_HOSTS = ['.topbar .header-actions', '.topbar', 'header'];

  const listeners = [];
  let current = DEFAULT_LANGUAGE;

  function readStored() {
    try {
      const stored = global.localStorage?.getItem(STORAGE_KEY);
      return LANGUAGES.includes(stored) ? stored : DEFAULT_LANGUAGE;
    } catch (error) {
      // A blocked storage partition must not break the interface.
      return DEFAULT_LANGUAGE;
    }
  }

  function writeStored(language) {
    try {
      global.localStorage?.setItem(STORAGE_KEY, language);
    } catch (error) {
      // Persisting the choice is a convenience, never a requirement.
    }
  }

  function lookup(key, fallback) {
    const table = TABLES[current] || {};
    if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
    const base = TABLES[DEFAULT_LANGUAGE];
    if (Object.prototype.hasOwnProperty.call(base, key)) return base[key];
    return fallback === undefined ? key : fallback;
  }

  function interpolate(template, params) {
    if (!params || typeof template !== 'string') return template;
    return template.replace(/\{(\w+)\}/g, (match, name) =>
      (Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match));
  }

  function translate(key, fallback, params) {
    return interpolate(lookup(key, fallback), params);
  }

  // A diagnostic that interpolated values into its own wording cannot match a
  // key exactly, so the catalog keys the pattern with `{}` where each value
  // went and the translation refers to them as `{0}`, `{1}`. Matching rebuilds
  // the pattern as a regular expression and reads the values back out. The
  // longest pattern wins, so a fully spelled-out message beats a looser one.
  function fillTemplate(table, message) {
    let best = null;
    Object.keys(table).forEach(key => {
      if (!key.startsWith(ERROR_PREFIX) || !key.includes('{}')) return;
      // A key of nothing but placeholders would match every message, itself
      // included, and the recursion below would never end.
      if (!key.slice(ERROR_PREFIX.length).split('{}').join('').trim()) return;
      if (best && key.length <= best.key.length) return;
      const pattern = key.slice(ERROR_PREFIX.length)
        .split('{}')
        .map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('(.+?)');
      const found = new RegExp(`^${pattern}$`).exec(message);
      if (found) best = {key, values: found.slice(1)};
    });
    if (!best) return null;
    // Some diagnostics quote another one: `line 4: name is required` names the
    // row and then repeats what was wrong with it. Translate each value in turn
    // so the sentence does not end in English, and leave alone anything the
    // catalog has no wording for.
    const values = best.values.map(value => translateKnown(table, value));
    return table[best.key].replace(/\{(\d+)\}/g, (whole, index) =>
      (values[Number(index)] === undefined ? whole : values[Number(index)]));
  }

  // Wording a client threw rather than a server sent: the same catalog, but no
  // frame around what is unkeyed. A browser message — a storage failure, a
  // decode failure — is platform text, and dressing it as a rejection of the
  // expert's action would misdescribe it.
  function translateThrown(message) {
    if (typeof message !== 'string' || !message) return message;
    return translateKnown(TABLES[current] || {}, message);
  }

  function translateKnown(table, message) {
    const exact = ERROR_PREFIX + message;
    if (Object.prototype.hasOwnProperty.call(table, exact)) return table[exact];
    const filled = fillTemplate(table, message);
    return filled === null ? message : filled;
  }

  // Server diagnostics arrive as English text on the wire, and the catalog keys
  // them by that text. Two rules differ from interface copy: the lookup never
  // falls back to the Chinese table, because an English reader has to keep the
  // wire wording, and a parameterized diagnostic is matched as a pattern.
  function translateError(message) {
    if (typeof message !== 'string' || !message) return message;
    const table = TABLES[current] || {};
    const exact = ERROR_PREFIX + message;
    if (Object.prototype.hasOwnProperty.call(table, exact)) return table[exact];
    const filled = fillTemplate(table, message);
    if (filled !== null) return filled;
    // Nothing keyed it. Frame it so a diagnostic we have not translated yet
    // still reads as a rejection rather than as interface copy, and keep the
    // wording itself intact so it can be reported verbatim.
    const frame = table[ERROR_PREFIX + '_frame'];
    return frame ? frame.replace('{detail}', message) : message;
  }

  // Vocabulary the viewer supplies over the iframe boundary: a function name, an
  // argument name, a choice in a dropdown, the sentence describing one, or a label
  // the viewer drew for itself. Keyed by that English text, and only in Chinese,
  // so a viewer we have no wording for still reads exactly as it declared itself.
  // The lookup answers `null` for that case, because a caller writing into the
  // viewer's own document has to know the difference between a gloss and the
  // English it would be overwriting.
  function viewerWording(kind, text) {
    const table = TABLES[current] || {};
    const key = VIEWER_PREFIX + kind + '.' + text;
    return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : null;
  }

  function translateViewer(kind, text) {
    if (typeof text !== 'string' || !text) return text;
    const wording = viewerWording(kind, text);
    return wording === null ? text : wording;
  }

  // ---------------------------------------------------------------------------
  // The wording a third-party viewer draws itself.
  //
  // Mol* ships no language layer: every label in its panel reaches the DOM in
  // English, and its bundle is a vendored release neither this repository nor the
  // operator may edit. Both delivery modes serve the viewer same-origin -- an
  // offline package from its own HTTP server, the online gateway from the
  // operator's registered viewer tree -- so the gloss is written from the parent,
  // over the frame's own text, and nothing in the foreign tree has to change.
  //
  // Every lookup is the wire-keyed one above: the English text is the key, so a
  // label we have no wording for is left exactly as the viewer drew it.

  // Attributes a viewer words for a reader rather than for the machine.
  const FRAME_TEXT_ATTRIBUTES = ['title', 'aria-label'];
  // Subtrees whose text is code, data or a drawing surface, never copy.
  const FRAME_OPAQUE_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CANVAS', 'SVG']);
  const SHOW_ELEMENT_AND_TEXT = 0x1 | 0x4;
  const FILTER_ACCEPT = 1;
  const FILTER_REJECT = 2;

  // `tagName` keeps the case it was written in outside HTML, so an inline <svg>
  // reads as 'svg'.
  function opaque(element) {
    return FRAME_OPAQUE_TAGS.has(String(element.tagName || '').toUpperCase());
  }

  // Mol* lays a label out across several lines; the key is the wording, not the
  // whitespace the layout happened to use.
  function collapse(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  // What we wrote, and the English we wrote it over. Kept so English restores the
  // viewer's own wording, and so a re-render that replaces a label is not
  // mistaken for a gloss of ours and glossed a second time.
  function glossed(memory, node, attribute) {
    const entry = memory.get(node);
    if (!entry) return null;
    return attribute === undefined ? entry : entry[attribute] || null;
  }

  function remember(memory, node, attribute, record) {
    if (attribute === undefined) {
      memory.set(node, record);
      return;
    }
    const entry = memory.get(node) || {};
    entry[attribute] = record;
    memory.set(node, entry);
  }

  function forget(memory, node, attribute) {
    if (attribute === undefined) {
      memory.delete(node);
      return;
    }
    const entry = memory.get(node);
    if (!entry) return;
    delete entry[attribute];
    if (!Object.keys(entry).length) memory.delete(node);
  }

  // Gloss one piece of text, or put the viewer's own wording back. `read` and
  // `write` hide whether it is a text node or an attribute; everything else --
  // when to restore, when to re-read the source -- is the same for both.
  function glossOne(memory, node, attribute, read, write) {
    const shown = read();
    const record = glossed(memory, node, attribute);
    // Our gloss still stands, so the English it replaced is the source. Anything
    // else means the viewer rewrote this label, and what it wrote is the source.
    const source = record && shown === record.written ? record.source : shown;
    const wording = viewerWording('frame', collapse(source));
    if (wording === null) {
      if (record) {
        if (shown === record.written) write(record.source);
        forget(memory, node, attribute);
      }
      return 0;
    }
    if (shown === wording) return 0;
    remember(memory, node, attribute, {source, written: wording});
    write(wording);
    return 1;
  }

  function glossText(memory, node) {
    return glossOne(
      memory, node, undefined,
      () => node.nodeValue,
      value => { node.nodeValue = value; },
    );
  }

  function glossAttributes(memory, element) {
    let written = 0;
    FRAME_TEXT_ATTRIBUTES.forEach(attribute => {
      if (!element.hasAttribute(attribute)) {
        forget(memory, element, attribute);
        return;
      }
      written += glossOne(
        memory, element, attribute,
        () => element.getAttribute(attribute),
        value => { element.setAttribute(attribute, value); },
      );
    });
    return written;
  }

  // Walk a subtree and gloss every label in it. Idempotent: a label already
  // glossed reads its recorded source and lands on the same wording.
  function glossSubtree(memory, root) {
    const doc = root.ownerDocument || root;
    if (!doc || typeof doc.createTreeWalker !== 'function') return 0;
    let written = 0;
    if (root.nodeType === 3) return glossText(memory, root);
    if (root.nodeType === 1) {
      if (opaque(root)) return 0;
      written += glossAttributes(memory, root);
    }
    const walker = doc.createTreeWalker(root, SHOW_ELEMENT_AND_TEXT, {
      acceptNode: node => (
        node.nodeType === 1 && opaque(node) ? FILTER_REJECT : FILTER_ACCEPT
      ),
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      written += node.nodeType === 3
        ? glossText(memory, node)
        : glossAttributes(memory, node);
    }
    return written;
  }

  // Nodes the frame has since dropped keep the memory growing for as long as the
  // task is open, so entries whose node left the document are released.
  function pruneMemory(memory) {
    Array.from(memory.keys()).forEach(node => {
      if (node.isConnected === false) memory.delete(node);
    });
  }

  /**
   * Gloss the wording inside a viewer frame, and keep glossing it as the viewer
   * re-renders. Returns a function that stops watching; calling it is enough,
   * because the frame navigating away discards the nodes with it.
   *
   * A frame we cannot reach into -- a viewer served from another origin -- keeps
   * its own wording, and this is a no-op.
   */
  function observeFrame(frame) {
    let doc = null;
    try {
      doc = frame && frame.contentDocument;
    } catch (error) {
      doc = null;
    }
    const root = doc && doc.documentElement;
    if (!root) return () => {};

    const memory = new Map();
    let observer = null;
    const view = frame.contentWindow;
    const Observer = (view && view.MutationObserver) || global.MutationObserver;

    // Our own writes come back as mutations. They gloss to the same wording, so
    // the second pass writes nothing and the loop closes after one turn.
    const handle = records => {
      records.forEach(record => {
        if (record.type === 'attributes') {
          glossAttributes(memory, record.target);
        } else if (record.type === 'characterData') {
          glossText(memory, record.target);
        } else {
          record.addedNodes.forEach(node => glossSubtree(memory, node));
        }
      });
      pruneMemory(memory);
    };

    glossSubtree(memory, root);
    if (Observer) {
      observer = new Observer(handle);
      observer.observe(root, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: FRAME_TEXT_ATTRIBUTES,
      });
    }

    // A language switch has to reach the frame too: restoring first, then
    // glossing, is what lets Chinese, English and Chinese again all be right.
    const relanguage = () => {
      if (!root.isConnected) return;
      glossSubtree(memory, root);
    };
    listeners.push(relanguage);

    return function stop() {
      if (observer) observer.disconnect();
      const index = listeners.indexOf(relanguage);
      if (index !== -1) listeners.splice(index, 1);
      memory.clear();
    };
  }

  function applyAttributes(element) {
    ATTRIBUTE_KEYS.forEach(([dataName, attribute]) => {
      const key = element.getAttribute(dataName);
      if (key) element.setAttribute(attribute, translate(key));
    });
  }

  function apply(root) {
    const scope = root || global.document;
    if (!scope) return;
    scope.querySelectorAll('[data-i18n]').forEach(element => {
      element.textContent = translate(element.getAttribute('data-i18n'));
    });
    ATTRIBUTE_KEYS.forEach(([dataName]) => {
      scope.querySelectorAll(`[${dataName}]`).forEach(applyAttributes);
    });
    const documentElement = global.document?.documentElement;
    if (documentElement) documentElement.setAttribute('lang', current);
  }

  function notify() {
    listeners.forEach(listener => {
      try {
        listener(current);
      } catch (error) {
        // One misbehaving subscriber must not stop the others.
      }
    });
  }

  function setLanguage(language) {
    if (!LANGUAGES.includes(language) || language === current) return;
    current = language;
    writeStored(language);
    apply();
    notify();
  }

  function nextLanguage() {
    const index = LANGUAGES.indexOf(current);
    return LANGUAGES[(index + 1) % LANGUAGES.length];
  }

  function toggleHost(doc) {
    for (const selector of TOGGLE_HOSTS) {
      const host = doc.querySelector(selector);
      if (host) return host;
    }
    return null;
  }

  function mountToggle() {
    const doc = global.document;
    if (!doc || doc.querySelector('.sv-lang-toggle')) return;
    const style = doc.createElement('style');
    style.textContent = TOGGLE_STYLE;
    doc.head.appendChild(style);
    const host = toggleHost(doc);
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = host ? 'sv-lang-toggle sv-lang-toggle-inline' : 'sv-lang-toggle';
    button.setAttribute('data-i18n', 'lang.switchTo');
    button.setAttribute('data-i18n-title', 'lang.switchTitle');
    button.addEventListener('click', () => setLanguage(nextLanguage()));
    (host || doc.body).appendChild(button);
    applyAttributes(button);
    button.textContent = translate('lang.switchTo');
  }

  function init() {
    current = readStored();
    apply();
    mountToggle();
  }

  global.SVI18n = {
    DEFAULT_LANGUAGE,
    LANGUAGES,
    language: () => current,
    setLanguage,
    t: translate,
    translateError,
    translateThrown,
    translateViewer,
    apply,
    observeFrame,
    onChange: listener => {
      if (typeof listener === 'function') listeners.push(listener);
    },
  };

  if (global.document?.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}(window));
