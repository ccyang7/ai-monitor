// 对应后端 app/schemas/project.py

export interface ProjectCreateIn {
  name: string
  description?: string | null
  tech_stack?: string | null
}

// PATCH 语义：字段「没传」= 不动它，「传 null」= 清空它。name 不能传 null
export interface ProjectUpdateIn {
  name?: string
  description?: string | null
  tech_stack?: string | null
}

export interface Project {
  id: number
  name: string
  description: string | null
  tech_stack: string | null
  status: string
  created_at: string
}

// 创建项目时后端会在同一次响应里给出第一把 Key 的明文，只此一次
export interface ProjectCreated extends Project {
  api_key: string
}

export interface ProjectListItem {
  id: number
  name: string
  tech_stack: string | null
  status: string
  // 该项目最近一次上报的时间；从没上报过则为 null
  last_called_at: string | null
  // 异常判定要到阶段 9 才有数据源，在那之前恒为 false
  has_anomaly: boolean
}

export interface ProjectListOut {
  items: ProjectListItem[]
  total: number
}
