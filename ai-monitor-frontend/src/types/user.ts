// 对应后端 app/schemas/user.py
export interface User {
  id: number
  username: string
  email: string | null
  // 'user' | 'admin'：只用于显示管理入口，真正的权限边界在后端 require_admin
  role: string
  created_at: string
}

// 对应后端 app/schemas/admin.py：管理后台的用户列表项
export interface AdminUserItem {
  id: number
  username: string
  email: string | null
  role: string
  created_at: string
  // 只给聚合数字，没有项目名 —— 管理员的可见范围止于账号信息
  project_count: number
  last_called_at: string | null
}

export interface AdminUserListOut {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}
