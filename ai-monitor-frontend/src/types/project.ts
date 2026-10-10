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
  // 数据表要到阶段 6 才有，现在恒为 null / false
  last_called_at: string | null
  has_anomaly: boolean
}

export interface ProjectListOut {
  items: ProjectListItem[]
  total: number
}
