<template>
  <el-form-item label="归属版本">
    <el-select v-model="form.target_version" filterable allow-create default-first-option clearable :disabled="linked" placeholder="版本号或前端、云端版、不跟版等" style="width:100%">
      <el-option v-for="v in versions" :key="v" :label="v" :value="v" />
    </el-select>
    <span v-if="linked" class="field-hint">已关联发版记录，版本随发版记录同步</span>
  </el-form-item>
  <el-form-item label="风险等级">
    <el-select v-model="form.risk_level" clearable placeholder="未评估" style="width:100%">
      <el-option v-for="(meta, key) in RISK_META" :key="key" :label="meta.label" :value="key" />
    </el-select>
  </el-form-item>
  <el-form-item label="当前卡点/进度"><el-input v-model="form.progress_note" type="textarea" :rows="3" maxlength="10000" /></el-form-item>
  <el-form-item label="预计完成"><el-date-picker v-model="form.expected_completion" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" /></el-form-item>
  <el-form-item label="下一步动作"><el-input v-model="form.next_action" type="textarea" :rows="2" maxlength="10000" /></el-form-item>
  <el-form-item label="动作负责人"><el-input v-model="form.action_owner" maxlength="128" placeholder="姓名或团队" /></el-form-item>
  <el-form-item label="最晚反馈时间"><el-date-picker v-model="form.feedback_due_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择日期和时间" /></el-form-item>
  <el-form-item label="是否影响发版">
    <el-radio-group v-model="form.affects_release"><el-radio :value="true">是</el-radio><el-radio :value="false">否</el-radio></el-radio-group>
    <el-button link @click="form.affects_release = null">{{ form.affects_release == null ? '未评估' : '清空' }}</el-button>
  </el-form-item>
  <el-form-item label="结论/备注"><el-input v-model="form.conclusion" type="textarea" :rows="3" maxlength="10000" /></el-form-item>
</template>

<script setup>
import { RISK_META } from '@/utils/taskLedger'
defineProps({ form: { type: Object, required: true }, versions: { type: Array, default: () => [] }, linked: Boolean })
</script>
<style scoped>.field-hint { color: var(--tech-muted); font-size: 12px; }</style>
