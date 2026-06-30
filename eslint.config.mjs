import js from '@eslint/js'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'

export default [
  {
    ignores: [
      '**/dist/',
      '**/build/',
      '**/.next/',
      '**/node_modules/',
      '**/coverage/',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs['flat/strict-type-checked'],
  ...tseslint.configs['flat/stylistic-type-checked'],
  {
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
]
