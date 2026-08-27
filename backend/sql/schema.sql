-- ============================================================
-- 测试管理平台 · MySQL 建表脚本 (schema.sql)
-- 适用 MySQL 8.0+
-- 字符集 utf8mb4。与后端 SQLAlchemy 模型一一对应。
-- ============================================================

CREATE DATABASE IF NOT EXISTS test_platform
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE test_platform;

-- ---------- 用户 ----------
CREATE TABLE `user` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(64) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `name` VARCHAR(64) NOT NULL,
  `email` VARCHAR(128) DEFAULT NULL,
  `is_platform_admin` TINYINT(1) NOT NULL DEFAULT 0,
  `status` ENUM('active','disabled') NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 项目 / 团队 / 成员 ----------
CREATE TABLE `project` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(128) NOT NULL,
  `code` VARCHAR(64) NOT NULL,
  `description` VARCHAR(512) DEFAULT NULL,
  `platform_type` VARCHAR(16) DEFAULT NULL,
  `status` ENUM('active','archived') NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `team` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `name` VARCHAR(64) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_team_project` (`project_id`),
  CONSTRAINT `fk_team_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `project_member` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT NOT NULL,
  `project_id` BIGINT NOT NULL,
  `team_id` BIGINT DEFAULT NULL,
  `role` ENUM('admin','member','guest') NOT NULL DEFAULT 'member',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_project` (`user_id`,`project_id`),
  KEY `idx_pm_user` (`user_id`),
  KEY `idx_pm_project` (`project_id`),
  CONSTRAINT `fk_pm_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_pm_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_pm_team` FOREIGN KEY (`team_id`) REFERENCES `team`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 每日任务分配 ----------
CREATE TABLE `task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `team_id` BIGINT DEFAULT NULL,
  `assigned_by` BIGINT NOT NULL,
  `assigned_to` BIGINT NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `description` TEXT,
  `module` VARCHAR(128) DEFAULT NULL,
  `priority` ENUM('p0','p1','p2','p3') NOT NULL DEFAULT 'p2',
  `assigned_date` DATE NOT NULL,
  `status` ENUM('pending','testing','blocked','online','closed') NOT NULL DEFAULT 'pending',
  `status_locked` TINYINT(1) NOT NULL DEFAULT 0,
  `online_at` DATETIME DEFAULT NULL,
  `closed_at` DATETIME DEFAULT NULL,
  `close_note` TEXT,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_project_date` (`project_id`,`assigned_date`),
  KEY `idx_assignee` (`assigned_to`,`assigned_date`),
  CONSTRAINT `fk_task_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_task_team` FOREIGN KEY (`team_id`) REFERENCES `team`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_task_by` FOREIGN KEY (`assigned_by`) REFERENCES `user`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_task_to` FOREIGN KEY (`assigned_to`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 日报反馈 ----------
CREATE TABLE `daily_report` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_id` BIGINT NOT NULL,
  `user_id` BIGINT NOT NULL,
  `project_id` BIGINT NOT NULL,
  `report_date` DATE NOT NULL,
  `progress_pct` TINYINT NOT NULL DEFAULT 0,
  `is_online` TINYINT(1) NOT NULL DEFAULT 0,
  `online_time` DATETIME DEFAULT NULL,
  `workload_hours` DECIMAL(5,1) NOT NULL DEFAULT 0.0,
  `summary` TEXT,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_date` (`task_id`,`report_date`),
  KEY `idx_report_project_date` (`project_id`,`report_date`),
  CONSTRAINT `fk_report_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_report_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_report_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 遗留问题 ----------
CREATE TABLE `remaining_issue` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `report_id` BIGINT DEFAULT NULL,
  `task_id` BIGINT DEFAULT NULL,
  `checklist_item_id` BIGINT DEFAULT NULL,
  `project_id` BIGINT NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `description` TEXT,
  `severity` ENUM('blocker','major','minor') NOT NULL DEFAULT 'minor',
  `status` ENUM('open','resolved') NOT NULL DEFAULT 'open',
  `owner` BIGINT DEFAULT NULL,
  `external_ref` VARCHAR(128) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `resolved_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_issue_project_status` (`project_id`,`status`),
  CONSTRAINT `fk_issue_report` FOREIGN KEY (`report_id`) REFERENCES `daily_report`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_issue_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_issue_owner` FOREIGN KEY (`owner`) REFERENCES `user`(`id`) ON DELETE SET NULL,
  KEY `idx_issue_task` (`task_id`),
  KEY `idx_issue_checklist` (`checklist_item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 验收清单（测试点回流任务） ----------
CREATE TABLE `checklist_item` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_id` BIGINT NOT NULL,
  `test_case_id` BIGINT NOT NULL,
  `project_id` BIGINT NOT NULL,
  `exec_status` ENUM('pending','passed','failed','blocked') NOT NULL DEFAULT 'pending',
  `executed_by` BIGINT DEFAULT NULL,
  `executed_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_checklist_task_case` (`task_id`,`test_case_id`),
  KEY `idx_checklist_task` (`task_id`),
  KEY `idx_checklist_case` (`test_case_id`),
  KEY `idx_checklist_project` (`project_id`),
  CONSTRAINT `fk_checklist_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_checklist_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_checklist_user` FOREIGN KEY (`executed_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 集成层（扩展位，P3 使用） ----------
CREATE TABLE `integration` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT DEFAULT NULL,
  `type` VARCHAR(32) NOT NULL,
  `config_json` JSON DEFAULT NULL,
  `credential_ref` VARCHAR(255) DEFAULT NULL,
  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_integration_project` (`project_id`),
  KEY `idx_integration_type` (`type`),
  CONSTRAINT `fk_integration_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `api_token` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT NOT NULL,
  `name` VARCHAR(64) NOT NULL,
  `token_hash` VARCHAR(255) NOT NULL,
  `scopes` JSON DEFAULT NULL,
  `expires_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_token_user` (`user_id`),
  CONSTRAINT `fk_token_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `integration_event` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `integration_id` BIGINT DEFAULT NULL,
  `source` VARCHAR(32) NOT NULL,
  `payload_json` JSON DEFAULT NULL,
  `received_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` ENUM('received','processed','failed') NOT NULL DEFAULT 'received',
  `error` TEXT,
  PRIMARY KEY (`id`),
  KEY `idx_event_source` (`source`),
  CONSTRAINT `fk_event_integration` FOREIGN KEY (`integration_id`) REFERENCES `integration`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- QA Copilot：AI 生成任务与测试点 ----------
CREATE TABLE `ai_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `task_id` BIGINT DEFAULT NULL,
  `user_id` BIGINT NOT NULL,
  `kind` VARCHAR(32) NOT NULL DEFAULT 'testcase_gen',
  `provider` VARCHAR(16) NOT NULL DEFAULT 'claude',
  `input_type` ENUM('text','url','file') NOT NULL DEFAULT 'text',
  `input_ref` TEXT,
  `status` ENUM('running','done','failed') NOT NULL DEFAULT 'running',
  `output_raw` TEXT,
  `error` TEXT,
  `case_count` INT NOT NULL DEFAULT 0,
  `cost_usd` DECIMAL(10,4) DEFAULT NULL,
  `output_tokens` INT DEFAULT NULL,
  `duration_ms` INT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_aitask_project` (`project_id`),
  KEY `idx_aitask_task` (`task_id`),
  KEY `idx_aitask_user` (`user_id`),
  KEY `idx_aitask_provider` (`provider`),
  CONSTRAINT `fk_aitask_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_aitask_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_aitask_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `test_case` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `ai_task_id` BIGINT NOT NULL,
  `provider` VARCHAR(16) NOT NULL DEFAULT 'claude',
  `project_id` BIGINT NOT NULL,
  `task_id` BIGINT DEFAULT NULL,
  `category` VARCHAR(32) DEFAULT NULL,
  `title` VARCHAR(512) NOT NULL,
  `steps` TEXT,
  `expected` TEXT,
  `priority` VARCHAR(8) DEFAULT NULL,
  `adopted` TINYINT(1) NOT NULL DEFAULT 0,
  `review_status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `reviewed_at` DATETIME DEFAULT NULL,
  `exec_kind` VARCHAR(8) NOT NULL DEFAULT 'gui',
  `platform` VARCHAR(16) NOT NULL DEFAULT 'web',
  `kind_reason` TEXT NULL,
  `script` TEXT NULL,
  `last_gen_error` TEXT NULL,
  `page` VARCHAR(255) NULL,
  `is_regression` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_testcase_aitask` (`ai_task_id`),
  KEY `idx_testcase_project` (`project_id`),
  KEY `idx_testcase_task` (`task_id`),
  KEY `idx_testcase_proj_review` (`project_id`,`review_status`),
  KEY `idx_testcase_reviewed` (`reviewed_at`),
  KEY `idx_testcase_provider` (`provider`),
  KEY `idx_testcase_regression` (`project_id`,`is_regression`),
  CONSTRAINT `fk_testcase_aitask` FOREIGN KEY (`ai_task_id`) REFERENCES `ai_task`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_testcase_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_testcase_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 执行队列（勾选用例下发目标机 → Claude Code 执行 → 回写）----------
-- payload 用 TEXT 存 JSON 字符串（不用原生 JSON 列，兼容 MySQL 5.6）。
-- checklist_item_id 是回写落点：runner 判 pass/fail 后同步对应清单项的 exec_status。
-- 放在 test_case / checklist_item 之后，保证被引用表先建。
CREATE TABLE `exec_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `checklist_item_id` BIGINT DEFAULT NULL,
  `test_case_id` BIGINT DEFAULT NULL,
  `task_id` BIGINT DEFAULT NULL,
  `project_id` BIGINT NOT NULL,
  `runner` VARCHAR(64) NOT NULL DEFAULT 'mac-01',
  `kind` ENUM('gui','api','cli','e2e','manual') NOT NULL DEFAULT 'gui',
  `status` ENUM('pending','running','passed','failed','blocked') NOT NULL DEFAULT 'pending',
  `payload` TEXT,
  `batch_id` VARCHAR(32) DEFAULT NULL,
  `verdict` VARCHAR(16) DEFAULT NULL,
  `fail_kind` VARCHAR(16) DEFAULT NULL,
  `reason` TEXT,
  `evidence_url` VARCHAR(512) DEFAULT NULL,
  `report` TEXT,
  `duration_ms` INT DEFAULT NULL,
  `enqueued_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_execrun_checklist` (`checklist_item_id`),
  KEY `idx_execrun_case` (`test_case_id`),
  KEY `idx_execrun_task` (`task_id`),
  KEY `idx_execrun_project` (`project_id`),
  KEY `idx_execrun_status` (`status`),
  KEY `idx_execrun_runner` (`runner`),
  KEY `idx_execrun_batch` (`batch_id`),
  CONSTRAINT `fk_execrun_checklist` FOREIGN KEY (`checklist_item_id`) REFERENCES `checklist_item`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_case` FOREIGN KEY (`test_case_id`) REFERENCES `test_case`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_execrun_user` FOREIGN KEY (`enqueued_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 对话测评题（发给被测大模型的 query 及执行参数）
CREATE TABLE `eval_query` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `task_id` BIGINT NULL,
  `ai_task_id` BIGINT NULL,
  `provider` VARCHAR(16) NOT NULL DEFAULT 'claude',
  `title` VARCHAR(512) NOT NULL,
  `dimension` VARCHAR(16) NULL,
  `prompt` TEXT NOT NULL,
  `attachments` TEXT NULL,
  `conversation_group` VARCHAR(64) NULL,
  `turn_index` INT NOT NULL DEFAULT 0,
  `dialog_options` TEXT NULL,
  `expected` TEXT NULL,
  `review_status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `reviewed_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_evalquery_project` (`project_id`),
  KEY `idx_evalquery_task` (`task_id`),
  KEY `idx_evalquery_aitask` (`ai_task_id`),
  KEY `idx_evalquery_provider` (`provider`),
  CONSTRAINT `fk_evalquery_project` FOREIGN KEY (`project_id`) REFERENCES `project` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_evalquery_task` FOREIGN KEY (`task_id`) REFERENCES `task` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_evalquery_aitask` FOREIGN KEY (`ai_task_id`) REFERENCES `ai_task` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 一次对话测评执行 + 判定结果（会话全过程轨迹 + 三维判定，合并一行）
CREATE TABLE `eval_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `eval_query_id` BIGINT NULL,
  `project_id` BIGINT NOT NULL,
  `batch_id` VARCHAR(32) NULL,
  `eval_task_id` BIGINT NULL,
  `runner` VARCHAR(64) NOT NULL DEFAULT 'mac-01',
  `device_kind` VARCHAR(8) NOT NULL DEFAULT 'web',
  `target_engine` VARCHAR(32) NULL,
  `target_device` VARCHAR(64) NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `payload` TEXT NULL,
  `session_id` VARCHAR(64) NULL,
  `share_link` VARCHAR(512) NULL,
  `artifact_share_link` VARCHAR(512) NULL,
  `answer` TEXT NULL,
  `trace` TEXT NULL,
  `reported_duration` VARCHAR(32) NULL,
  `bean_cost` VARCHAR(32) NULL,
  `tokens` VARCHAR(32) NULL,
  `verdict` VARCHAR(16) NULL,
  `verdict_dims` TEXT NULL,
  `verdict_reason` TEXT NULL,
  `judged_by` VARCHAR(16) NULL,
  `is_abnormal` TINYINT(1) NOT NULL DEFAULT 0,
  `pushed_multica` TINYINT(1) NOT NULL DEFAULT 0,
  `multica_ref` VARCHAR(512) NULL,
  `reason` TEXT NULL,
  `duration_ms` INT NULL,
  `enqueued_by` BIGINT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_evalrun_query` (`eval_query_id`),
  KEY `idx_evalrun_project` (`project_id`),
  KEY `idx_evalrun_batch` (`batch_id`),
  KEY `idx_evalrun_runner` (`runner`),
  KEY `idx_evalrun_status` (`status`),
  KEY `idx_evalrun_abnormal` (`is_abnormal`),
  CONSTRAINT `fk_evalrun_query` FOREIGN KEY (`eval_query_id`) REFERENCES `eval_query` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_evalrun_project` FOREIGN KEY (`project_id`) REFERENCES `project` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_evalrun_user` FOREIGN KEY (`enqueued_by`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 对话测评任务(一组定制用例集合,可整体执行+AI综合评价)
CREATE TABLE `eval_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `name` VARCHAR(128) NOT NULL,
  `description` TEXT NULL,
  `query_ids` TEXT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'draft',
  `last_batch_id` VARCHAR(32) NULL,
  `summary_html` TEXT NULL,
  `summary_status` VARCHAR(16) NULL,
  `summary_provider` VARCHAR(16) NULL,
  `summary_at` DATETIME NULL,
  `created_by` BIGINT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_evaltask_project` (`project_id`),
  CONSTRAINT `fk_evaltask_project` FOREIGN KEY (`project_id`) REFERENCES `project` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_evaltask_user` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 执行机连上的纳米 Work 客户端可切换设备(vm)快照:CLI 上报,前端下发时下拉选
CREATE TABLE `eval_client_device` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `runner` VARCHAR(64) NOT NULL,
  `vm_id` VARCHAR(64) NOT NULL,
  `label` VARCHAR(96) NULL,
  `name` VARCHAR(128) NULL,
  `status` VARCHAR(16) NULL,
  `device_type` INT NULL,
  `last_report_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_eval_device_runner_vm` (`runner`,`vm_id`),
  KEY `idx_eval_device_runner` (`runner`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 执行设备:平台成员登记的自有执行机(runner);每设备独立 token,下发只选自己的设备。
CREATE TABLE `runner_device` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `owner_id` BIGINT NOT NULL,
  `runner_id` VARCHAR(64) NOT NULL,
  `name` VARCHAR(128) NOT NULL,
  `platform` VARCHAR(16) NOT NULL DEFAULT 'web',
  `token` VARCHAR(128) NOT NULL,
  `last_seen_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_owner_runner` (`owner_id`,`runner_id`),
  UNIQUE KEY `uk_device_token` (`token`),
  KEY `idx_device_owner` (`owner_id`),
  KEY `idx_runnerdev_platform` (`platform`),
  CONSTRAINT `fk_device_owner` FOREIGN KEY (`owner_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- release records
CREATE TABLE `release_record` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `version` VARCHAR(64) NOT NULL,
  `sub_product` VARCHAR(32) DEFAULT NULL,
  `channel` VARCHAR(255) DEFAULT NULL,
  `release_date` DATE NOT NULL,
  `req_count` INT NOT NULL DEFAULT 0,
  `content` TEXT,
  `memo` TEXT,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_release_project_date` (`project_id`,`release_date`),
  CONSTRAINT `fk_release_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_release_user` FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 语义选择器注册表（单一事实源）：按 project_id + sub_product 分域
-- candidates 用 TEXT 存 JSON 字符串（MySQL 5.6 无原生 JSON）
CREATE TABLE IF NOT EXISTS `selector_key` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `project_id` INT NOT NULL,
  `sub_product` VARCHAR(32) NOT NULL DEFAULT '',
  `platform` VARCHAR(16) NOT NULL DEFAULT 'web',
  `key` VARCHAR(64) NOT NULL,
  `frame` VARCHAR(128) NOT NULL DEFAULT 'auto',
  `page` VARCHAR(64) NOT NULL DEFAULT '',
  `desc` VARCHAR(255) NOT NULL DEFAULT '',
  `candidates` TEXT,
  `updated_by` INT DEFAULT NULL,
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_selkey_scope_key` (`project_id`,`sub_product`,`key`),
  KEY `idx_selkey_scope` (`project_id`,`sub_product`),
  KEY `idx_selkey_platform` (`platform`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 每个 (project_id, sub_product) 域的 vm_iframe 配置
CREATE TABLE IF NOT EXISTS `selector_scope` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `project_id` INT NOT NULL,
  `sub_product` VARCHAR(32) NOT NULL DEFAULT '',
  `vm_iframe` VARCHAR(255) NOT NULL DEFAULT '',
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_selscope` (`project_id`,`sub_product`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 设备探测请求：平台下发探测 → runner 拉取执行 → 回写 result/error
-- params/result 用 TEXT 存 JSON 字符串（MySQL 5.6 无原生 JSON）
CREATE TABLE IF NOT EXISTS `probe_request` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `project_id` INT NOT NULL,
  `sub_product` VARCHAR(32) NOT NULL DEFAULT '',
  `runner` VARCHAR(64) NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `params` TEXT,
  `result` LONGTEXT,   -- 探测结果 JSON 可达 200KB+（复杂页数百元素），超 TEXT 64KB 上限，用 LONGTEXT
  `error` VARCHAR(500) DEFAULT NULL,
  `screenshot_path` VARCHAR(255) DEFAULT NULL,
  `created_by` INT DEFAULT NULL,
  `created_at` DATETIME DEFAULT NULL,
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_probe_project` (`project_id`),
  KEY `idx_probe_runner` (`runner`),
  KEY `idx_probe_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 项目级 api 测试环境（被测业务系统的 base_url + 鉴权 + 接口契约）
-- auth_json/contract 用 TEXT 存 JSON 字符串（MySQL 5.6 无原生 JSON）
CREATE TABLE IF NOT EXISTS `api_env` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `base_url` VARCHAR(255) NOT NULL DEFAULT '',
  `auth_type` VARCHAR(16) NOT NULL DEFAULT 'fixed',
  `auth_json` TEXT,
  `contract` TEXT,
  `updated_by` BIGINT DEFAULT NULL,
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_apienv_project` (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 性能报告集:把若干次采集归入一个可命名、独立展示的报告(下发/上传前建或选)。
CREATE TABLE `perf_report_set` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(128) NOT NULL,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_perfset_user` FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 性能测试记录:nami-perfdog 采集结果载体 + 下发单。双轨 source(dispatch 下发 / upload 本地直传)。
-- 状态/场景用 VARCHAR(不用 ENUM):规避 MySQL 原生 ENUM 越界静默空串的坑,且场景名可自定义扩展。
CREATE TABLE `perf_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT DEFAULT NULL,
  `report_set_id` BIGINT DEFAULT NULL,
  `runner` VARCHAR(64) NOT NULL DEFAULT 'win-01',
  `scenario` VARCHAR(32) NOT NULL,
  `variant` VARCHAR(64) NOT NULL DEFAULT 'default',
  `proc` VARCHAR(128) DEFAULT NULL,
  `duration` VARCHAR(16) DEFAULT NULL,
  `source` VARCHAR(16) NOT NULL DEFAULT 'dispatch',
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `outcome` VARCHAR(16) DEFAULT NULL,
  `meta_json` TEXT,
  `samples_json` LONGTEXT,
  `events_json` TEXT,
  `duration_ms` INT DEFAULT NULL,
  `error` TEXT,
  `prompt` TEXT,
  `signal_seq` INT NOT NULL DEFAULT 0,
  `started_at` DATETIME DEFAULT NULL,
  `ended_at` DATETIME DEFAULT NULL,
  `enqueued_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_perfrun_project` (`project_id`),
  KEY `idx_perfrun_reportset` (`report_set_id`),
  KEY `idx_perfrun_runner` (`runner`),
  KEY `idx_perfrun_scenario` (`scenario`),
  KEY `idx_perfrun_variant` (`variant`),
  KEY `idx_perfrun_status` (`status`),
  CONSTRAINT `fk_perfrun_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_perfrun_reportset` FOREIGN KEY (`report_set_id`) REFERENCES `perf_report_set`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_perfrun_user` FOREIGN KEY (`enqueued_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 反馈测试模块（机器人推 md/zip → 结构化用例 → 回归集 → 下发 exec_run）----------
-- 与 test_case 体系完全隔离；exec_run 零改动（靠专用项目 project_id 隔离 + batch_id 聚合）。
-- 结构化数据（script/steps/expected/feedback_summary）用 TEXT 存，兼容 MySQL 5.6。
CREATE TABLE `feedback_import` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `source_bot` VARCHAR(64) DEFAULT NULL,
  `filename` VARCHAR(255) DEFAULT NULL,
  `file_count` INT NOT NULL DEFAULT 0,
  `case_count` INT NOT NULL DEFAULT 0,
  `status` VARCHAR(16) NOT NULL DEFAULT 'parsing',
  `script_done` INT NOT NULL DEFAULT 0,
  `script_total` INT NOT NULL DEFAULT 0,
  `note` TEXT,
  `error` TEXT,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_fbimport_project` (`project_id`),
  CONSTRAINT `fk_fbimport_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feedback_case` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `import_id` BIGINT NOT NULL,
  `project_id` BIGINT NOT NULL,
  `req_title` VARCHAR(512) DEFAULT NULL,
  `req_url` VARCHAR(1024) DEFAULT NULL,
  `feedback_summary` TEXT,
  `point_code` VARCHAR(32) DEFAULT NULL,
  `point_title` VARCHAR(255) DEFAULT NULL,
  `case_no` VARCHAR(16) DEFAULT NULL,
  `title` VARCHAR(512) NOT NULL,
  `precondition` TEXT,
  `steps` TEXT,
  `expected` TEXT,
  `category` VARCHAR(16) DEFAULT NULL,
  `priority` VARCHAR(8) DEFAULT NULL,
  `auto_feasible` VARCHAR(8) NOT NULL DEFAULT 'no',
  `auto_reason` TEXT,
  `exec_kind` VARCHAR(8) NOT NULL DEFAULT 'manual',
  `script` TEXT,
  `script_error` TEXT,
  `page` VARCHAR(255) DEFAULT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'draft',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_fbcase_import` (`import_id`),
  KEY `idx_fbcase_project` (`project_id`),
  KEY `idx_fbcase_status` (`status`),
  CONSTRAINT `fk_fbcase_import` FOREIGN KEY (`import_id`) REFERENCES `feedback_import`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_fbcase_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feedback_regression_set` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `description` TEXT,
  `schedule_cron` VARCHAR(64) DEFAULT NULL,
  `schedule_enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `runner` VARCHAR(64) NOT NULL DEFAULT 'mac-01',
  `last_run_at` DATETIME DEFAULT NULL,
  `next_run_at` DATETIME DEFAULT NULL,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_fbset_project` (`project_id`),
  CONSTRAINT `fk_fbset_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_fbset_user` FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feedback_set_case` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `set_id` BIGINT NOT NULL,
  `case_id` BIGINT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_feedback_set_case` (`set_id`,`case_id`),
  KEY `idx_fbsetcase_set` (`set_id`),
  KEY `idx_fbsetcase_case` (`case_id`),
  CONSTRAINT `fk_fbsetcase_set` FOREIGN KEY (`set_id`) REFERENCES `feedback_regression_set`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_fbsetcase_case` FOREIGN KEY (`case_id`) REFERENCES `feedback_case`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feedback_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `set_id` BIGINT DEFAULT NULL,
  `batch_id` VARCHAR(32) NOT NULL,
  `trigger` VARCHAR(8) NOT NULL DEFAULT 'manual',
  `case_count` INT NOT NULL DEFAULT 0,
  `started_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_fbrun_project` (`project_id`),
  KEY `idx_fbrun_set` (`set_id`),
  KEY `idx_fbrun_batch` (`batch_id`),
  CONSTRAINT `fk_fbrun_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_fbrun_set` FOREIGN KEY (`set_id`) REFERENCES `feedback_regression_set`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_fbrun_user` FOREIGN KEY (`started_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 上线 checklist（漏斗末端：回归用例库勾选出的待上线验证用例，每项目一份） ----------
CREATE TABLE `release_checklist_item` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `test_case_id` BIGINT NOT NULL,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_release_checklist_proj_case` (`project_id`,`test_case_id`),
  KEY `idx_relck_project` (`project_id`),
  KEY `idx_relck_case` (`test_case_id`),
  CONSTRAINT `fk_relck_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_relck_case` FOREIGN KEY (`test_case_id`) REFERENCES `test_case`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_relck_user` FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 种子数据：默认平台管理员 admin / admin123 （生产请改密）
-- password_hash = bcrypt('admin123')，首次启动后端也会用同样逻辑种入。
-- bcrypt 哈希需由后端生成；这里建议首次启动后端自动种入，而非手写哈希。
-- 若需手动种入，可执行后端：python -c "from app.core.security import hash_password; print(hash_password('admin123'))"
-- ============================================================
