import { http } from './client'
import type { AdminAssistantOut } from '@/types/assistant'
import type { AdminUnpricedModel, Model, ModelCreateIn, ModelUpdateIn } from '@/types/model'
import type { AdminUserListOut } from '@/types/user'

// 这些接口全部需要管理员权限，后端对非 admin 一律返回 403
export async function fetchAdminModels(): Promise<Model[]> {
  const { data } = await http.get<Model[]>('/admin/models')
  return data
}

export interface AdminUserListParams {
  page: number
  page_size: number
  q?: string
}

export async function fetchAdminUsers(params: AdminUserListParams): Promise<AdminUserListOut> {
  // 空搜索词不传：后端用 `(q or "").strip()` 也挡得住，但少一个参数少一层歧义
  const { data } = await http.get<AdminUserListOut>('/admin/users', {
    params: { ...params, q: params.q?.trim() || undefined },
  })
  return data
}

export interface AdminAssistantParams {
  page: number
  page_size: number
  // 不传 = 全部时间；axios 会把 undefined 的键整个丢掉
  days?: number
}

export async function fetchAdminAssistantUsage(
  params: AdminAssistantParams,
): Promise<AdminAssistantOut> {
  const { data } = await http.get<AdminAssistantOut>('/admin/assistant', { params })
  return data
}

// 级联删除：用户的项目、Key、调用日志、助手用量与自建模型会一并消失
export async function deleteAdminUser(userId: number): Promise<void> {
  await http.delete(`/admin/users/${userId}`)
}

export async function fetchGlobalUnpricedModels(): Promise<AdminUnpricedModel[]> {
  const { data } = await http.get<AdminUnpricedModel[]>('/admin/models/unpriced')
  return data
}

export async function createBuiltinModel(payload: ModelCreateIn): Promise<Model> {
  const { data } = await http.post<Model>('/admin/models', payload)
  return data
}

// 后端只认 is_builtin 的行：传用户自建的 id 会得到 404
export async function updateBuiltinModel(
  modelId: number,
  payload: ModelUpdateIn,
): Promise<Model> {
  const { data } = await http.patch<Model>(`/admin/models/${modelId}`, payload)
  return data
}

// 物理删除价格行；历史调用记录与它们的成本快照不受影响
export async function deleteBuiltinModel(modelId: number): Promise<void> {
  await http.delete(`/admin/models/${modelId}`)
}
