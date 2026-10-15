import { http } from './client'
import type { Model, ModelCreateIn, ModelListOut, ModelUpdateIn } from '@/types/model'

// 路径写 '/models' 不带尾斜杠：后端把集合路径定义成 ""，多加一个斜杠会吃 307 重定向
export async function fetchModels(): Promise<ModelListOut> {
  const { data } = await http.get<ModelListOut>('/models')
  return data
}

export async function createModel(payload: ModelCreateIn): Promise<Model> {
  const { data } = await http.post<Model>('/models', payload)
  return data
}

// 后端只认自建模型：内置一律 404（要改内置价走「生成副本」那条路）
export async function updateModel(modelId: number, payload: ModelUpdateIn): Promise<Model> {
  const { data } = await http.patch<Model>(`/models/${modelId}`, payload)
  return data
}

// 后端是软删除（status 改成 disabled），不是物理删除
export async function disableModel(modelId: number): Promise<void> {
  await http.delete(`/models/${modelId}`)
}
