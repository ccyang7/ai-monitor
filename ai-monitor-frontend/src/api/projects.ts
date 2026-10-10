import { http } from './client'
import type { ApiKey, ApiKeyCreated } from '@/types/api_key'
import type {
  Project,
  ProjectCreateIn,
  ProjectCreated,
  ProjectListOut,
  ProjectUpdateIn,
} from '@/types/project'

// 路径写 '/projects' 不带尾斜杠：后端把集合路径定义成 ""，多加一个斜杠会吃 307 重定向
export async function fetchProjects(): Promise<ProjectListOut> {
  const { data } = await http.get<ProjectListOut>('/projects')
  return data
}

export async function fetchProject(projectId: number): Promise<Project> {
  const { data } = await http.get<Project>(`/projects/${projectId}`)
  return data
}

export async function createProject(payload: ProjectCreateIn): Promise<ProjectCreated> {
  const { data } = await http.post<ProjectCreated>('/projects', payload)
  return data
}

// 只发真正改过的字段：后端按「没传就不动」处理，多传了反而有清空的风险
export async function updateProject(
  projectId: number,
  payload: ProjectUpdateIn,
): Promise<Project> {
  const { data } = await http.patch<Project>(`/projects/${projectId}`, payload)
  return data
}

export async function deleteProject(projectId: number): Promise<void> {
  await http.delete(`/projects/${projectId}`)
}

export async function fetchApiKeys(projectId: number): Promise<ApiKey[]> {
  const { data } = await http.get<ApiKey[]>(`/projects/${projectId}/api-keys`)
  return data
}

export async function createApiKey(projectId: number): Promise<ApiKeyCreated> {
  const { data } = await http.post<ApiKeyCreated>(`/projects/${projectId}/api-keys`)
  return data
}

// 后端是软吊销（status 改成 revoked），不是物理删除
export async function revokeApiKey(keyId: number): Promise<void> {
  await http.delete(`/api-keys/${keyId}`)
}
