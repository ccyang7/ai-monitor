import type { GlobalThemeOverrides } from 'naive-ui'

// naive-ui 的组件不认 CSS 变量，只认这个对象。所以原型的令牌要在两处各写一遍
// （这里 + styles/tokens.css），改配色时两个文件都得动
const FONT_FAMILY =
  "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif"

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#40916C',
    // 原型里按钮和链接的 hover 都是【变深】而不是变亮，naive 默认相反，这里掰回来
    primaryColorHover: '#24563F',
    primaryColorPressed: '#1B4332',
    primaryColorSuppl: '#40916C',

    infoColor: '#40916C',
    successColor: '#40916C',
    warningColor: '#D98E2B',
    errorColor: '#C4453C',

    textColor1: '#0F2A22',
    textColor2: '#2C3E37',
    textColor3: '#6B7F76',

    borderColor: '#E3EAE6',
    dividerColor: '#E3EAE6',

    borderRadius: '10px',
    borderRadiusSmall: '6px',

    fontFamily: FONT_FAMILY,
    fontFamilyMono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '14px',
    // 原型输入框量出来约 42px，naive 默认 34px —— 不调的话表单会比手写控件矮一截
    heightMedium: '40px',

    bodyColor: '#F4F7F5',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    inputColor: '#FFFFFF',

    boxShadow2: '0 1px 2px rgba(11, 35, 28, .04), 0 4px 14px rgba(11, 35, 28, .05)',
  },

  Select: {
    peers: {
      InternalSelectMenu: {
        borderRadius: '10px',
      },
    },
  },

  // NModal 会把 n-modal 这个类合并到弹窗根元素上，所以阴影得由它给；
  // 手写样式那边就不能再写 box-shadow 了，否则两条同名规则谁生效取决于打包顺序
  Modal: {
    color: '#FFFFFF',
    textColor: '#2C3E37',
    boxShadow: '0 12px 40px rgba(11, 35, 28, .22)',
  },

  // 同上：n-drawer 这个类也是 naive 合并到面板根元素上的
  Drawer: {
    color: '#FFFFFF',
    textColor: '#2C3E37',
    boxShadow: '-10px 0 44px rgba(11, 35, 28, .16)',
    // 原型里的抽屉是贴边的直角面板，naive 默认给左侧两个圆角
    borderRadius: '0',
    // 抽屉内部自己排版（标题栏 + 内容），naive 的默认内边距要归零
    bodyPadding: '0',
  },
}
