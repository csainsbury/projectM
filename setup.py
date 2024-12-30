from setuptools import setup, find_packages

setup(
    name="kairos-pm",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'flask',
        'flask-cors',
        'python-dotenv',
        'todoist-api-python',
        'PyGithub',
        'jsonlines',
        'requests'
    ],
    entry_points={
        'console_scripts': [
            'kairos=kairos.core.project_manager:main',
        ],
    },
    author="Your Name",
    author_email="your.email@example.com",
    description="Kairos Project Management System",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/kairos-pm",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
) 