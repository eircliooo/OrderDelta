import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // 硬约束 #2：API base 必须是相对路径 /api，禁止硬编码后端 origin。
      'no-restricted-syntax': [
        'error',
        {
          selector: "Literal[value=/https?:\\/\\/(localhost|127\\.0\\.0\\.1)/]",
          message: '禁止硬编码后端地址，API 一律用相对路径 /api（Vite dev proxy 转发）。',
        },
      ],
    },
  },
  {
    files: ['tests/**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
  {
    // vite.config.ts 是**唯一**允许写出后端 origin 的地方（dev proxy 的 target）。
    files: ['vite.config.ts'],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      'no-restricted-syntax': 'off',
    },
  },
)
