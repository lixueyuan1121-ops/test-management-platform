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
  CONSTRAINT `fk_aitask_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_aitask_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_aitask_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `test_case` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `ai_task_id` BIGINT NOT NULL,
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
  `kind_reason` TEXT NULL,
  `script` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_testcase_aitask` (`ai_task_id`),
  KEY `idx_testcase_project` (`project_id`),
  KEY `idx_testcase_task` (`task_id`),
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
  `kind` ENUM('gui','api','cli') NOT NULL DEFAULT 'gui',
  `status` ENUM('pending','running','passed','failed') NOT NULL DEFAULT 'pending',
  `payload` TEXT,
  `verdict` VARCHAR(16) DEFAULT NULL,
  `reason` TEXT,
  `evidence_url` VARCHAR(512) DEFAULT NULL,
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
  CONSTRAINT `fk_execrun_checklist` FOREIGN KEY (`checklist_item_id`) REFERENCES `checklist_item`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_case` FOREIGN KEY (`test_case_id`) REFERENCES `test_case`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_execrun_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_execrun_user` FOREIGN KEY (`enqueued_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 种子数据：默认平台管理员 admin / admin123 （生产请改密）
-- password_hash = bcrypt('admin123')，首次启动后端也会用同样逻辑种入。
-- bcrypt 哈希需由后端生成；这里建议首次启动后端自动种入，而非手写哈希。
-- 若需手动种入，可执行后端：python -c "from app.core.security import hash_password; print(hash_password('admin123'))"
-- ============================================================
