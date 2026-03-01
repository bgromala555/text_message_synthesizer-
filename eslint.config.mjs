import eslint from '@eslint/js';
import importPlugin from 'eslint-plugin-import';
import jsdocPlugin from 'eslint-plugin-jsdoc';
import sonarjs from 'eslint-plugin-sonarjs';
import tseslint from 'typescript-eslint';

export default tseslint.config(
    eslint.configs.recommended,
    ...tseslint.configs.strictTypeChecked,
    ...tseslint.configs.stylisticTypeChecked,
    sonarjs.configs.recommended,
    {
        languageOptions: {
            parserOptions: {
                projectService: true,
                tsconfigRootDir: import.meta.dirname,
            },
        },
        plugins: {
            import: importPlugin,
            jsdoc: jsdocPlugin,
        },
        rules: {
            // ============================================
            // Type-aware rules
            // ============================================
            '@typescript-eslint/no-floating-promises': 'error',
            '@typescript-eslint/no-misused-promises': 'error',
            '@typescript-eslint/await-thenable': 'error',
            '@typescript-eslint/no-unnecessary-type-assertion': 'error',
            '@typescript-eslint/prefer-nullish-coalescing': 'error',
            '@typescript-eslint/prefer-optional-chain': 'error',
            '@typescript-eslint/strict-boolean-expressions': 'off',
            '@typescript-eslint/no-unsafe-assignment': 'error',
            '@typescript-eslint/no-unsafe-member-access': 'error',
            '@typescript-eslint/no-unsafe-call': 'error',
            '@typescript-eslint/no-unsafe-return': 'error',

            // ============================================
            // Enforce consistency
            // ============================================
            '@typescript-eslint/consistent-type-imports': [
                'error',
                {
                    prefer: 'type-imports',
                    fixStyle: 'inline-type-imports',
                },
            ],
            '@typescript-eslint/consistent-type-exports': 'error',
            '@typescript-eslint/method-signature-style': ['error', 'property'],

            // ============================================
            // Explicit is better than implicit
            // ============================================
            '@typescript-eslint/explicit-function-return-type': [
                'error',
                {
                    allowExpressions: true,
                    allowTypedFunctionExpressions: true,
                },
            ],
            '@typescript-eslint/explicit-member-accessibility': [
                'error',
                {
                    accessibility: 'explicit',
                    overrides: { constructors: 'no-public' },
                },
            ],

            // ============================================
            // Ban dangerous patterns
            // ============================================
            '@typescript-eslint/no-explicit-any': 'error',
            '@typescript-eslint/no-non-null-assertion': 'error',
            '@typescript-eslint/no-confusing-void-expression': 'error',
            '@typescript-eslint/restrict-template-expressions': [
                'error',
                { allowNumber: true },
            ],

            // ============================================
            // Async best practices
            // ============================================
            '@typescript-eslint/require-await': 'error',
            '@typescript-eslint/return-await': ['error', 'always'],

            // ============================================
            // Prefer immutability and clean code
            // ============================================
            '@typescript-eslint/prefer-readonly': 'error',
            '@typescript-eslint/no-unnecessary-condition': 'error',

            // ============================================
            // Exhaustiveness and correctness
            // ============================================
            '@typescript-eslint/switch-exhaustiveness-check': 'error',
            '@typescript-eslint/promise-function-async': 'error',
            '@typescript-eslint/no-redundant-type-constituents': 'error',
            '@typescript-eslint/no-deprecated': 'warn',

            // ============================================
            // Naming conventions
            // ============================================
            '@typescript-eslint/naming-convention': [
                'error',
                { selector: 'interface', format: ['PascalCase'] },
                { selector: 'typeAlias', format: ['PascalCase'] },
                { selector: 'class', format: ['PascalCase'] },
                { selector: 'enum', format: ['PascalCase'] },
                { selector: 'enumMember', format: ['UPPER_CASE'] },
            ],

            // ============================================
            // Core JS safety and style
            // ============================================
            'eqeqeq': ['error', 'always'],
            'no-eval': 'error',
            'no-implied-eval': 'error',
            'no-new-wrappers': 'error',
            'curly': ['error', 'all'],
            'no-else-return': ['error', { allowElseIf: false }],
            'prefer-const': 'error',
            'no-console': 'off',
            'no-debugger': 'error',

            // ============================================
            // Complexity and modularity (DRY enforcement)
            // ============================================
            complexity: ['error', { max: 10 }],
            'max-depth': ['error', { max: 5 }],
            'max-lines-per-function': [
                'error',
                {
                    max: 75,
                    skipBlankLines: true,
                    skipComments: true,
                },
            ],
            'max-params': ['error', { max: 5 }],

            // ============================================
            // Import sorting and organization
            // ============================================
            'import/order': [
                'error',
                {
                    groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
                    'newlines-between': 'always',
                    alphabetize: { order: 'asc' },
                },
            ],
            'import/no-duplicates': 'error',

            // ============================================
            // JSDoc documentation enforcement
            // ============================================
            'jsdoc/require-jsdoc': [
                'error',
                {
                    require: {
                        FunctionDeclaration: true,
                        MethodDefinition: true,
                        ClassDeclaration: true,
                        ArrowFunctionExpression: false,
                        FunctionExpression: false,
                    },
                    publicOnly: true,
                },
            ],
            'jsdoc/require-description': 'error',
            'jsdoc/require-param-description': 'error',
            'jsdoc/require-returns-description': 'error',
            'jsdoc/check-param-names': 'error',
            'jsdoc/check-types': 'error',

            // ============================================
            // SonarJS code smell detection
            // ============================================
            'sonarjs/no-duplicate-string': ['error', { threshold: 3 }],
            'sonarjs/cognitive-complexity': ['error', 15],
            'sonarjs/no-identical-functions': 'error',
        },
    },
    {
        // Disable JSDoc rules for type declaration files
        files: ['**/*.d.ts'],
        rules: {
            'jsdoc/require-jsdoc': 'off',
            'jsdoc/require-description': 'off',
            'jsdoc/require-param-description': 'off',
            'jsdoc/require-returns-description': 'off',
        },
    },
    {
        // Relax rules for test files
        files: ['**/__tests__/**/*.ts', '**/*.test.ts'],
        rules: {
            'max-lines-per-function': 'off',
            'sonarjs/no-duplicate-string': 'off',
            'jsdoc/require-jsdoc': 'off',
            'jsdoc/require-description': 'off',
        },
    },
    {
        // Ignore patterns
        ignores: [
            'node_modules/**',
            'static/**/*.js',
            'static/**/*.js.map',
        ],
    }
);
